"""What `fit_to_palette` produces today, pinned byte for byte."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from pipeline.definitive.pixelize import MATCH_METHODS, fit_to_palette

PALETTE = [(0, 0, 0), (34, 32, 52), (69, 40, 60), (102, 57, 49),
           (143, 86, 59), (223, 113, 38), (217, 160, 102), (238, 195, 154)]


def _image(edge: int, seed: int = 0) -> np.ndarray:
    """A narrow-range subject, which is the case the stretch exists for."""
    rng = np.random.default_rng(seed)
    ramp = np.linspace(86, 170, edge, dtype=np.float32)
    field = (ramp[None, :] * 0.5 + ramp[:, None] * 0.5)[..., None] * np.array(
        [1.0, 0.8, 0.6])
    return np.clip(field + rng.normal(0, 4, (edge, edge, 3)), 0, 255).astype(np.uint8)


def _digest(arr: np.ndarray) -> str:
    return hashlib.blake2b(np.ascontiguousarray(arr).tobytes(),
                           digest_size=8).hexdigest()


def test_the_stretch_widens_the_range_it_was_given():
    """The reason the layer exists: without it the snapped image keeps the
    subject's own narrow band instead of reaching the palette's ends."""
    image = _image(64)
    from pipeline.definitive.pixelize import apply_fixed_palette

    plain = apply_fixed_palette(image, PALETTE)
    fitted = fit_to_palette(image, PALETTE)

    assert len(np.unique(fitted.reshape(-1, 3), axis=0)) >= \
        len(np.unique(plain.reshape(-1, 3), axis=0)), \
        "fitting used no more of the palette than plain snapping"


@pytest.mark.parametrize("method", sorted(MATCH_METHODS))
def test_each_matching_method_is_pinned(method):
    """Byte-exact. A scheduling change must not move a single pixel."""
    expected = {
        "lab": "b8bd5b5ce13214c1",
        "luma": "7f3b2eaa5623544e",
        "rgb": "d8a934282fa48e4c",
        "weighted": "6e00c16888d14158",
    }
    got = _digest(fit_to_palette(_image(48), PALETTE, method=method))
    assert got == expected[method], (
        f"{method} produced {got}, pinned as {expected[method]}")


def test_strength_below_one_interpolates_back():
    image = _image(32)
    full = fit_to_palette(image, PALETTE, strength=1.0)
    none = fit_to_palette(image, PALETTE, strength=0.0)
    half = fit_to_palette(image, PALETTE, strength=0.5)
    assert not np.array_equal(full, none), "strength had no effect"
    assert half.shape == image.shape and half.dtype == np.uint8


def test_an_alpha_mask_measures_the_subject_not_the_backdrop():
    """The range is taken from opaque pixels, so a backdrop cannot widen it."""
    image = _image(32)
    image[:8, :] = 255
    alpha = np.full(image.shape[:2], 255, np.uint8)
    alpha[:8, :] = 0
    masked = fit_to_palette(image, PALETTE, alpha=alpha)
    whole = fit_to_palette(image, PALETTE, alpha=None)
    assert not np.array_equal(masked, whole), "the mask was ignored"


def test_an_empty_palette_returns_the_image_untouched():
    image = _image(16)
    assert fit_to_palette(image, []) is image


def test_a_flat_image_falls_back_to_plain_snapping():
    """A span of nothing cannot be stretched onto anything."""
    from pipeline.definitive.pixelize import apply_fixed_palette

    flat = np.full((16, 16, 3), 120, np.uint8)
    assert np.array_equal(fit_to_palette(flat, PALETTE),
                          apply_fixed_palette(flat, PALETTE))


def test_a_fully_transparent_mask_still_produces_a_picture():
    image = _image(16)
    out = fit_to_palette(image, PALETTE, alpha=np.zeros((16, 16), np.uint8))
    assert out.shape == image.shape and out.dtype == np.uint8


def test_the_result_is_always_drawn_from_the_palette():
    out = fit_to_palette(_image(40), PALETTE)
    used = {tuple(int(v) for v in c) for c in np.unique(out.reshape(-1, 3), axis=0)}
    assert used <= set(PALETTE), f"{used - set(PALETTE)} is not in the palette"


@pytest.mark.parametrize("edge", (16, 33, 64))
def test_shape_and_dtype_survive_every_size(edge):
    out = fit_to_palette(_image(edge), PALETTE)
    assert out.shape == (edge, edge, 3)
    assert out.dtype == np.uint8


def _peak(fn):
    import gc
    import tracemalloc

    gc.collect()
    tracemalloc.start()
    try:
        fn()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        gc.collect()
    return peak


def test_every_chunk_size_produces_the_same_picture():
    """Splitting the work must not split the answer."""
    image = _image(96)
    digests = {_digest(fit_to_palette(image, PALETTE, chunk=c))
               for c in (256, 1000, 4096, 1 << 20)}
    assert len(digests) == 1, f"chunk size changed the picture: {digests}"


def test_the_stretch_costs_no_more_than_the_snap_beneath_it():
    """The stretch used to hold nine full-size float arrays at once."""
    from pipeline.definitive.pixelize import apply_fixed_palette

    image = _image(256)
    stretch = _peak(lambda: fit_to_palette(image, PALETTE))
    snap = _peak(lambda: apply_fixed_palette(image, PALETTE))

    assert stretch < snap * 1.5, (
        f"fitting peaked at {stretch / 1e6:.2f}MB against the snap's "
        f"{snap / 1e6:.2f}MB - the stretch is still holding the whole image")


class TestBackdropColour:
    """A backdrop the user names, in the forms a person actually types."""

    def test_rgb_with_commas(self):
        from pipeline.definitive.builtin import parse_colour

        assert parse_colour("12, 34, 56") == (12, 34, 56)
        assert parse_colour("12,34,56") == (12, 34, 56)
        assert parse_colour("rgb(12, 34, 56)") == (12, 34, 56)

    def test_hex_long_and_short(self):
        from pipeline.definitive.builtin import parse_colour

        assert parse_colour("#0a1b2c") == (10, 27, 44)
        assert parse_colour("0a1b2c") == (10, 27, 44)
        assert parse_colour("#abc") == (170, 187, 204)

    def test_blank_means_flood_from_a_corner(self):
        from pipeline.definitive.builtin import parse_colour

        assert parse_colour("") is None
        assert parse_colour(None) is None
        assert parse_colour("   ") is None

    def test_a_channel_over_255_is_refused(self):
        from pipeline.definitive.builtin import parse_colour
        from pipeline.shared.errors import Invalid

        with pytest.raises(Invalid):
            parse_colour("300, 0, 0")

    def test_nonsense_is_refused_by_name(self):
        from pipeline.definitive.builtin import parse_colour
        from pipeline.shared.errors import Invalid

        with pytest.raises(Invalid) as caught:
            parse_colour("chartreuse")
        assert caught.value.detail["field"] == "colour"


class TestBackdropNaming:
    """A prompt CLIP can read, and a hex the keyer can match."""

    def test_the_prompt_names_the_colour_rather_than_coding_it(self):
        from pipeline.looks import vocabulary

        said = vocabulary.backdrop_prompt("#FF00FF")
        assert "magenta" in said
        assert "#FF00FF" not in said, "a hex code reaches CLIP as digits"

    def test_every_preset_has_a_name(self):
        from pipeline.shared.colour import BACKDROP_PRESETS, name_for

        for hex_value, _ in BACKDROP_PRESETS:
            assert name_for(hex_value).strip()

    def test_a_custom_colour_takes_its_nearest_name(self):
        from pipeline.shared.colour import name_for

        assert name_for("#fe02f0") == "magenta"
        assert name_for("12, 200, 60") == "bright green"

    def test_the_keyer_still_gets_the_exact_hex(self):
        """The prompt is approximate on purpose; the key must not be."""
        from pipeline.looks import vocabulary

        assert vocabulary.backdrop_colour({"colour": "#00B140"}) == "#00B140"

    def test_no_background_block_is_not_a_crash(self):
        from pipeline.looks import vocabulary

        assert vocabulary.backdrop_colour(None) == vocabulary.BACKDROP


def test_flattening_puts_the_keyed_colour_behind_a_sprite(tmp_path):
    """kohya composites alpha onto something undocumented, and that becomes
    the backdrop the LoRA learns. This makes it the one we key out."""
    import importlib.util
    import pathlib

    import numpy as np
    from PIL import Image

    spec = importlib.util.spec_from_file_location(
        "flatten_training", pathlib.Path("tools/flatten_training.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    art = np.zeros((32, 32, 4), dtype=np.uint8)
    art[8:24, 8:24] = (20, 200, 40, 255)
    src = tmp_path / "s.png"
    Image.fromarray(art, "RGBA").save(src)

    share = mod.flatten(src, tmp_path / "out.png", (255, 0, 255))
    out = np.asarray(Image.open(tmp_path / "out.png").convert("RGB"))
    assert tuple(out[0, 0]) == (255, 0, 255), "the backdrop is not the keyed colour"
    assert tuple(out[16, 16]) == (20, 200, 40), "the subject was altered"
    assert share == pytest.approx(256 / 1024, abs=0.01)


def test_a_palette_stops_at_what_the_image_has():
    """Asking for sixteen from an image with two returns two, not two padded."""
    import numpy as np

    from pipeline.definitive.pixelize import extract_palette

    flat = np.zeros((4, 4, 3), np.uint8)
    flat[:2] = (10, 20, 30)
    flat[2:] = (200, 180, 160)
    assert len(extract_palette(flat, 16)) == 2


def test_a_small_distinct_colour_survives_a_crowd_of_similar_ones():
    """Clustering weights a centre by the pixels it owns, so gold trim on a
    blue robe loses to the blues unless the entries are thinned by distance."""
    import numpy as np

    from pipeline.definitive.pixelize import extract_palette

    art = np.zeros((40, 40, 3), np.uint8)
    rng = np.random.default_rng(0)
    art[:] = np.stack([rng.integers(30, 60, (40, 40)),
                       rng.integers(60, 90, (40, 40)),
                       rng.integers(120, 150, (40, 40))], axis=2)
    art[19:21, 19:21] = (230, 190, 60)

    pal = extract_palette(art, 8)
    assert any(c[0] > c[2] + 40 for c in pal), f"the gold was merged away: {pal}"
