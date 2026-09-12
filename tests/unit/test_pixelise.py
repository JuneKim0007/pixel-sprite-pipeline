"""Handing the model the grid it will not draw on its own."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from pipeline.definitive.pixelize import estimate_block_size
from pipeline.stages.pixelise import blocked


def noisy(path, size=256):
    """Fine detail everywhere, which is what the model actually produces."""
    rng = np.random.default_rng(0)
    a = rng.integers(0, 255, (size, size, 3), dtype=np.uint8)
    Image.fromarray(a).save(path)
    return path


def test_quantising_puts_the_grid_where_it_was_asked_for(tmp_path):
    """The model measured 1.75 to 2.00 on a 1024 canvas that wanted 8."""
    src = noisy(tmp_path / "src.png")
    dst = tmp_path / "out.png"
    blocked(src, dst, 8)
    a = np.asarray(Image.open(dst).convert("RGB"))
    assert estimate_block_size(a) == pytest.approx(8.0)


def test_the_canvas_keeps_its_size(tmp_path):
    """The latent has to match what the sampler was configured for."""
    src = noisy(tmp_path / "src.png", size=256)
    dst = tmp_path / "out.png"
    blocked(src, dst, 8)
    assert Image.open(dst).size == (256, 256)


def test_the_cell_count_is_the_sprite_it_will_become(tmp_path):
    src = noisy(tmp_path / "src.png", size=256)
    assert blocked(src, tmp_path / "o.png", 8) == (32, 32)
    assert blocked(src, tmp_path / "p.png", 4) == (64, 64)


def test_every_block_is_one_colour(tmp_path):
    """A block that is not flat is a grid the reduction will average again."""
    src = noisy(tmp_path / "src.png", size=64)
    dst = tmp_path / "out.png"
    blocked(src, dst, 8)
    a = np.asarray(Image.open(dst).convert("RGB"))
    for y in range(0, 64, 8):
        for x in range(0, 64, 8):
            block = a[y:y + 8, x:x + 8].reshape(-1, 3)
            assert len(np.unique(block, axis=0)) == 1


def test_headroom_is_handed_over_rather_than_asked_for(tmp_path):
    """The sampler fills whatever canvas it is given - a guide at 0.815, 0.706 and."""
    from pipeline.geometry.framing import measure
    from pipeline.stages.pixelise import blocked

    src = tmp_path / "full.png"
    a = np.full((256, 256, 3), (240, 90, 150), dtype=np.uint8)
    a[0:256, 60:200] = (20, 20, 40)          # subject touching top and bottom
    Image.fromarray(a).save(src)
    assert set(measure(src).clipped) == {"top", "bottom"}

    dst = tmp_path / "framed.png"
    blocked(src, dst, 8, 0.82)
    after = measure(dst)
    assert after.clipped == [], "the re-render would clip again"
    assert after.fill == pytest.approx(0.82, abs=0.04)


def test_headroom_of_zero_leaves_the_framing_alone(tmp_path):
    from pipeline.geometry.framing import measure
    from pipeline.stages.pixelise import blocked

    src = noisy(tmp_path / "src.png", size=256)
    dst = tmp_path / "out.png"
    blocked(src, dst, 8, 0.0)
    assert Image.open(dst).size == (256, 256)


def _grid_image(factor=8, cells=40, seed=3):
    """Art actually drawn on a lattice, offset so phase 0 is the wrong answer."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (cells, cells, 3)).astype(np.uint8)
    big = np.repeat(np.repeat(small, factor, 0), factor, 1)
    return Image.fromarray(big)


def test_framing_does_not_resample_the_art(tmp_path):
    """Any non-integer rescale smears the grid: LANCZOS and NEAREST measured
    within 1% of each other, both 4x muddier than not rescaling at all."""
    import numpy as np
    from PIL import Image

    from pipeline.definitive.pixelize import find_phase
    from pipeline.stages.pixelise import framed

    factor = 8
    art = _grid_image(factor)
    canvas = Image.new("RGB", (art.width + 160, art.height + 160), (240, 90, 150))
    canvas.paste(art, (80, 80))

    out = framed(canvas, 0.5, factor)
    assert find_phase(np.asarray(out), factor) == (0, 0)


def _keyed_figure(factor=4, cells=64, key=(255, 0, 255)):
    """A soft-edged subject on a key colour, which is what a cut sheet gives us."""
    import numpy as np

    rng = np.random.default_rng(11)
    # Cool and warm tones a character is actually made of, none of them near the key.
    tones = np.array([(40, 52, 70), (100, 137, 155), (131, 164, 174), (252, 238, 227),
                      (175, 148, 136), (134, 112, 105), (83, 94, 108), (212, 215, 206)],
                     np.uint8)
    edge = cells * factor
    art = np.repeat(np.repeat(tones[rng.integers(0, len(tones), (cells, cells))],
                              factor, 0), factor, 1).astype(np.float32)

    y, x = np.ogrid[:edge, :edge]
    solid = (((y - edge / 2) / (edge * 0.34)) ** 2
             + ((x - edge / 2) / (edge * 0.26)) ** 2) <= 1.0
    # Anti-aliasing over a few pixels: the edge cells are then part key, part art.
    cover = solid.astype(np.float32)
    for _ in range(3):
        pad = np.pad(cover, 1, mode="edge")
        cover = sum(pad[a:a + edge, b:b + edge]
                    for a in range(3) for b in range(3)) / 9.0
    under = np.empty_like(art)
    under[:] = np.asarray(key, np.float32)
    return (art * cover[..., None] + under * (1 - cover[..., None])).round().astype(np.uint8)


def test_a_cell_that_touches_the_key_does_not_earn_a_palette_slot():
    """A half-keyed edge cell reads as subject, and its blended colour then gets
    a slot and is painted back onto the silhouette. Measured on 3 of 8 sheets."""
    import numpy as np

    from pipeline.definitive.pixelize import _blocks, generate_palette, project, reduce_blocks
    from pipeline.geometry.framing import key_backdrop

    factor, key = 4, (255, 0, 255)
    arr = _keyed_figure(factor, key=key)
    keyed = key_backdrop(arr) == 0
    small = reduce_blocks(arr, factor, 0, 0, "median", 32.0)
    share = _blocks(keyed[..., None].astype(np.uint8), factor, 0, 0)[..., 0].mean(axis=(2, 3))

    near_key = lambda pal: min(  # noqa: E731
        float(np.sqrt(((project(np.asarray([c], np.float32), "lab")
                        - project(np.asarray([key], np.float32), "lab")) ** 2).sum()))
        for c in pal)

    mostly = generate_palette(small, 16, method="lab",
                              alpha=((share <= 0.5) * 255).astype(np.uint8))
    clean = generate_palette(small, 16, method="lab",
                             alpha=((share == 0.0) * 255).astype(np.uint8))
    assert near_key(mostly) < 60, "the sample never picked up the key; widen the feather"
    assert near_key(clean) > 60, "a key-coloured entry survived a clean-cell sample"


def test_the_snap_space_cannot_silently_disagree_with_the_palette(tmp_path):
    """Building in one space and matching in another reassigns cells to a centre
    their cluster was not built around: 8.4% of coloured cells changed hue."""
    import inspect

    from pipeline.definitive.pixelize import apply_fixed_palette

    method = inspect.signature(apply_fixed_palette).parameters["method"]
    assert method.default is inspect.Parameter.empty, \
        "apply_fixed_palette took a default match space again"
