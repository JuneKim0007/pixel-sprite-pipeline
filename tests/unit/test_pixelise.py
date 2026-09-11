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
