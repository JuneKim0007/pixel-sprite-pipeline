"""The bounds that keep a render from taking the machine down."""

from __future__ import annotations

import numpy as np
import pytest

from pipeline import definitive
from pipeline.definitive import pngstream
from pipeline.definitive.layers import REGISTRY, admit, projected_pixels
from pipeline.shared import limits
from pipeline.shared.errors import TooLarge


def _stack(**scale_cfg):
    """The default stack with the Scale layer reconfigured."""
    return [dict(e, config={**e["config"], **scale_cfg})
            if e["layer"] == "scale" else e
            for e in definitive.default_stack()]


@pytest.mark.parametrize("sent,want", [
    (4, 4),
    (16, 16),
    (64, 16),
    (10 ** 9, 16),
    ("8", 8),
    (-3, 1),
    (None, 4),
    ("abc", 4),
])
def test_a_field_s_declared_bounds_bind_the_api_not_just_the_form(sent, want):
    # A request is not a form: the server took whatever arrived, so a control declaring max=16 accepted 64, which is 16x the pixels.
    assert REGISTRY["scale"].settings({"upscale": sent})["upscale"] == want


def test_an_unknown_config_key_survives_clamping():
    assert REGISTRY["scale"].settings({"nonsense": 7})["nonsense"] == 7


def test_growth_compounds_along_the_stack():
    # Checking the final size rather than the product would let two layers that each double slip through as 2x when they are 16x.
    assert projected_pixels(_stack(upscale=4), 1_000) == 16_000
    assert projected_pixels(_stack(upscale=16), 1_000) == 256_000


def test_a_deferred_layer_is_not_charged_for():
    assert projected_pixels(_stack(upscale=16), 1_000, {"scale"}) == 1_000


def test_a_disabled_layer_is_not_charged_for():
    off = [dict(e, enabled=False) if e["layer"] == "scale" else e
           for e in _stack(upscale=16)]
    assert projected_pixels(off, 1_000) == 1_000


def test_a_stack_too_big_is_refused_before_anything_is_allocated():
    with pytest.raises(TooLarge) as caught:
        admit(_stack(upscale=16), 4096 * 4096)
    # 413 and not 400: nothing is malformed, it simply does not fit.
    assert caught.value.status == 413
    assert caught.value.detail.get("field") == "scale"


def test_the_refusal_happens_in_apply_stack_itself():
    image = np.zeros((4096, 4096, 3), np.uint8)
    with pytest.raises(TooLarge):
        definitive.apply_stack(image, _stack(upscale=16))


def test_a_layer_that_grows_without_declaring_it_is_caught_on_output():
    spec = REGISTRY["scale"]
    liar = type(spec)(key="liar", label="L", summary="", fields=spec.fields,
                      apply=lambda img, cfg, facts, prep:
                          np.zeros((9000, 9000, 4), np.uint8))
    assert liar.growth({}) == 1.0, "the point is that it claims not to grow"
    with pytest.raises(TooLarge, match="produced"):
        liar.apply(np.zeros((8, 8, 3), np.uint8), {}, {}, {})


def test_deferring_magnification_gives_the_identical_picture():
    src = np.random.default_rng(0).integers(0, 256, (48, 48, 3)).astype(np.uint8)
    stack = _stack(upscale=8)

    small, facts = definitive.apply_stack(src, stack, defer={"scale"})
    full, _ = definitive.apply_stack(src, _stack(upscale=8), defer=set())

    assert (facts["deferred"]["width"], facts["deferred"]["height"]) == \
           (full.shape[1], full.shape[0])
    assert np.array_equal(np.repeat(np.repeat(small, 8, 0), 8, 1), full)


def test_only_a_deferrable_layer_can_be_deferred():
    # Otherwise "defer" becomes a way to silently skip work that actually changes the picture.
    src = np.zeros((16, 16, 3), np.uint8)
    _, facts = definitive.apply_stack(src, definitive.default_stack(),
                                      defer={"palette"})
    assert "palette" not in facts["deferred"]["layers"]


def test_a_deferred_run_does_not_poison_the_snapshot_cache():
    src = np.random.default_rng(1).integers(0, 256, (32, 32, 3)).astype(np.uint8)
    stack = _stack(upscale=4)
    definitive.apply_stack(src, stack, source="x@32x32", defer={"scale"})
    full, _ = definitive.apply_stack(src, stack, source="x@32x32", defer=set())
    assert full.shape[0] == 32 * 4, "a full run resumed from a deferred snapshot"


@pytest.mark.parametrize("channels", [3, 4])
@pytest.mark.parametrize("zoom", [1, 2, 3, 8])
def test_streamed_png_is_identical_to_the_array_it_never_built(
        tmp_path, channels, zoom):
    from PIL import Image

    src = np.random.default_rng(0).integers(
        0, 256, (17, 23, channels)).astype(np.uint8)
    path = tmp_path / "out.png"
    width, height = pngstream.write_scaled(path, src, zoom)

    got = np.array(Image.open(path).convert("RGBA" if channels == 4 else "RGB"))
    want = np.repeat(np.repeat(src, zoom, 0), zoom, 1)
    assert (width, height) == (want.shape[1], want.shape[0])
    assert np.array_equal(got, want)


def test_the_encoder_s_peak_does_not_grow_with_height(tmp_path):
    import tracemalloc

    peaks = []
    for rows in (64, 1024):
        src = np.zeros((rows, 128, 4), np.uint8)
        tracemalloc.start()
        pngstream.write_scaled(tmp_path / f"h{rows}.png", src, 8)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()

    short, tall = peaks
    assert tall < short * 1.5, (
        f"16x the rows cost {tall / short:.1f}x the memory, so the peak "
        f"tracks height after all")

    # And it is small in absolute terms: the case that panicked the machine cost 16 GB materialised.
    assert pngstream.peak_bytes(4096, 4, 16) < 200 << 20


def test_writing_a_magnified_file_never_materialises_it(tmp_path):
    import tracemalloc

    src = np.random.default_rng(0).integers(0, 256, (256, 256, 4)).astype(np.uint8)
    tracemalloc.start()
    pngstream.write_scaled(tmp_path / "big.png", src, 16)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    materialised = (256 * 16) ** 2 * 4
    assert peak < materialised / 10, (
        f"peaked at {peak / 2**20:.1f} MB writing an image that is "
        f"{materialised / 2**20:.1f} MB materialised")


def test_output_ceiling_is_derived_from_the_machine_not_hardcoded():
    before = limits.get("output_pixels")
    limits._STATE["output_share"] = limits.SHARES["output_share"] * 2
    try:
        assert limits.get("output_pixels") == pytest.approx(before * 2, rel=0.01)
    finally:
        limits._STATE["output_share"] = limits.SHARES["output_share"]


def test_the_rss_ceiling_leaves_the_machine_room_to_survive():
    assert limits.get("rss_bytes") < limits.memory_bytes() * 0.5


def test_a_source_over_the_ceiling_is_refused_before_it_is_decoded(tmp_path,
                                                                   monkeypatch):
    from PIL import Image

    from pipeline.api import editor as editor_mod

    path = tmp_path / "wide.png"
    Image.new("RGB", (64, 64)).save(path)
    monkeypatch.setattr(editor_mod.limits, "get",
                        lambda name: 1 if name == "output_pixels" else 1)
    with pytest.raises(TooLarge, match="MP"):
        editor_mod._open_bounded(path)


def test_a_file_pil_refuses_to_open_becomes_a_413_not_a_500(tmp_path,
                                                            monkeypatch):
    from PIL import Image

    from pipeline.api import editor as editor_mod

    path = tmp_path / "bomb.png"
    Image.new("RGB", (64, 64)).save(path)

    def explode(*args, **kwargs):
        raise Image.DecompressionBombError("too big")

    monkeypatch.setattr(Image, "open", explode)
    with pytest.raises(TooLarge) as caught:
        editor_mod._open_bounded(path)
    assert caught.value.status == 413
    assert caught.value.hint, "a refusal with no way forward is a dead end"
