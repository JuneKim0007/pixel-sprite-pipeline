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
