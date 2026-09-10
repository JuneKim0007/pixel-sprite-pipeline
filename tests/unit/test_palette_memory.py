"""A palette must cost what its chunk costs, not what the image measures."""

from __future__ import annotations

import gc
import tracemalloc

import numpy as np
import pytest

from pipeline.definitive.pixelize import (_distinct, generate_palette,
                                          palette_chunk, working_bytes)


def _image(edge: int, seed: int = 0) -> np.ndarray:
    """Anti-aliased content: nearly every pixel a distinct colour, which is
    the worst case for a palette and the one real renders produce."""
    rng = np.random.default_rng(seed)
    ramp = np.linspace(0, 255, edge, dtype=np.float32)
    field = (ramp[None, :] * 0.6 + ramp[:, None] * 0.4)[..., None] * np.array(
        [1.0, 0.7, 0.4])
    return np.clip(field + rng.normal(0, 8, (edge, edge, 3)), 0, 255).astype(np.uint8)


def _peak(fn):
    gc.collect()
    tracemalloc.start()
    try:
        fn()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        gc.collect()
    return peak


def test_the_chunk_size_comes_from_the_project_s_own_limit():
    from pipeline.shared import limits

    assert palette_chunk() == max(256, limits.get("colour_chunk"))
    assert palette_chunk(4096) == 4096
    assert palette_chunk(1) == 256, "a chunk below the floor is raised to it"


def test_distinct_counts_what_a_row_wise_unique_would():
    for edge in (8, 32, 64):
        pixels = _image(edge).reshape(-1, 3)
        assert _distinct(pixels) == len(np.unique(pixels, axis=0))


def test_every_chunk_size_produces_the_same_palette():
    """Chunking splits the work, not the answer."""
    image = _image(64)
    whole = generate_palette(image, 8, chunk=1 << 20)
    for chunk in (256, 512, 1000, 4096):
        assert generate_palette(image, 8, chunk=chunk) == whole


def test_a_palette_is_stable_across_methods_and_chunk_sizes():
    image = _image(48)
    for method in ("rgb", "weighted", "luma", "lab"):
        whole = generate_palette(image, 6, method=method, chunk=1 << 20)
        split = generate_palette(image, 6, method=method, chunk=300)
        assert whole == split, f"{method} disagreed across chunk sizes"


def test_the_full_colour_range_works_on_a_small_image():
    image = _image(64)
    palette = generate_palette(image, 256, chunk=512)
    assert 1 <= len(palette) <= 256
    assert all(len(c) == 3 and all(0 <= v <= 255 for v in c) for c in palette)


def test_the_cost_does_not_follow_the_colour_count():
    """The decisive one."""
    image = _image(256)
    peaks = {k: _peak(lambda k=k: generate_palette(image, k, chunk=512))
             for k in (8, 128)}

    assert peaks[128] < peaks[8] * 1.5, (
        f"peak went {peaks[8] / 1e6:.2f}MB -> {peaks[128] / 1e6:.2f}MB for 16x "
        f"the colours; K is still multiplying the whole image")


def test_the_cost_per_pixel_stays_flat_as_the_image_grows():
    """What remains is honest O(N) work - features, distances, the pixels
    themselves. It must stay a flat cost per pixel rather than a rising one."""
    image_small, image_large = _image(128), _image(256)
    per_pixel_small = _peak(
        lambda: generate_palette(image_small, 16, chunk=512)) / image_small[..., 0].size
    per_pixel_large = _peak(
        lambda: generate_palette(image_large, 16, chunk=512)) / image_large[..., 0].size

    assert per_pixel_large <= per_pixel_small * 1.2, (
        f"cost per pixel rose from {per_pixel_small:.0f} to "
        f"{per_pixel_large:.0f} bytes - something still scales worse than O(N)")


def test_the_working_set_is_bounded_by_the_chunk_and_nothing_else():
    chunk, colours = 1024, 32
    bound = working_bytes(chunk, colours)
    assert bound == chunk * colours * 3 * 4 + chunk * colours * 4

    assert bound < 3 << 20, f"{bound} bytes is not a bounded working set"


@pytest.mark.parametrize("edge", (32, 96))
def test_a_palette_still_describes_the_picture(edge):
    """Chunking must not change what the palette is for."""
    image = _image(edge)
    palette = generate_palette(image, 8)
    assert 1 <= len(palette) <= 8
    flat = image.reshape(-1, 3).astype(int)
    lo, hi = flat.min(axis=0), flat.max(axis=0)
    for colour in palette:
        assert all(lo[i] - 1 <= colour[i] <= hi[i] + 1 for i in range(3))


def test_a_uniform_image_still_yields_a_colour():
    flat = np.full((16, 16, 3), 77, np.uint8)
    assert generate_palette(flat, 8) == [(77, 77, 77)]


def test_an_image_with_no_opaque_pixels_is_refused_by_name():
    image = _image(16)
    with pytest.raises(ValueError):
        generate_palette(image, 4, alpha=np.zeros((16, 16), np.uint8))


def test_the_default_stack_does_not_broadcast_over_a_whole_image():
    """The layer, not just the function: a large frame must stay bounded."""
    from pipeline import definitive

    edge = 512
    image = np.dstack([_image(edge), np.full((edge, edge, 1), 255, np.uint8)])
    stack = [e for e in definitive.default_stack() if e["layer"] == "palette"]
    stack[0]["config"]["colours"] = 32

    peak = _peak(lambda: definitive.apply_stack(image, stack, defer={"scale"}))

    unchunked = edge * edge * 32 * 16
    assert peak < unchunked / 4, (
        f"peak {peak / 1e6:.1f}MB approaches the {unchunked / 1e6:.1f}MB an "
        f"N x K x D broadcast would have cost")


class TestCountColours:
    """The facts bar's colour count, which every preview pays for twice."""

    def test_it_is_exact_across_shapes(self):
        import numpy as np

        from pipeline.definitive.cache import count_colours

        rng = np.random.default_rng(0)
        for edge, k, channels in [(64, 7, 3), (64, 7, 4), (128, 300, 3)]:
            palette = rng.integers(0, 255, (k, 3), dtype=np.uint8)
            image = palette[rng.integers(0, k, (edge, edge))]
            if channels == 4:
                image = np.dstack([image, np.full((edge, edge), 255, np.uint8)])
            want = len(np.unique(image.reshape(-1, image.shape[2])[:, :3], axis=0))
            assert count_colours(image) == want

    def test_alpha_is_not_a_colour(self):
        import numpy as np

        from pipeline.definitive.cache import count_colours

        rgb = np.zeros((8, 8, 3), dtype=np.uint8)
        varied = np.dstack([rgb, np.arange(64, dtype=np.uint8).reshape(8, 8)])
        assert count_colours(varied) == 1

    def test_it_does_not_sample(self):
        """It used to take every seventh pixel over 400k and under-report."""
        import numpy as np

        from pipeline.definitive.cache import count_colours

        # 700k pixels, every one a different colour in the low bits.
        n = 700_000
        values = np.arange(n, dtype=np.uint32)
        flat = np.stack([(values >> 16) & 255, (values >> 8) & 255, values & 255],
                        axis=1).astype(np.uint8)
        image = flat.reshape(-1, 1, 3)
        assert count_colours(image) == n

    def test_a_flat_image_is_one_colour(self):
        import numpy as np

        from pipeline.definitive.cache import count_colours

        assert count_colours(np.full((32, 32, 3), 7, dtype=np.uint8)) == 1
