"""Where `find_phase` says the lattice starts, pinned before it is rewritten.

Sampling on the wrong phase straddles block boundaries and smears two logical
pixels into one, so the answer is the whole value of the measurement. These pin
it exactly, across shapes, channel counts and factors, so a change to how the
search is computed can be proved to leave the answer alone.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.definitive.pixelize import find_phase
from pipeline.shared.errors import Invalid

EXPECTED = {
    "lattice8": {2: (0, 0), 3: (1, 1), 4: (0, 0), 5: (3, 3), 6: (4, 4),
                 8: (0, 0), 12: (4, 4), 16: (8, 8)},
    "lattice6_rgba": {2: (0, 0), 3: (0, 0), 4: (2, 2), 5: (1, 1), 6: (0, 0),
                      8: (4, 6), 12: (6, 6), 16: (6, 2)},
    "noise_rgb": {2: (1, 1), 3: (2, 1), 4: (1, 1), 5: (4, 2), 6: (1, 4),
                  8: (1, 5), 12: (1, 11), 16: (12, 15)},
    "noise_rgba": {2: (1, 1), 3: (2, 2), 4: (1, 2), 5: (3, 4), 6: (1, 2),
                   8: (2, 1), 12: (6, 5), 16: (13, 1)},
    "gradient": {2: (1, 1), 3: (1, 1), 4: (1, 1), 5: (1, 2), 6: (1, 1),
                 8: (5, 7), 12: (1, 6), 16: (9, 15)},
    "flat": {2: (0, 0), 3: (0, 0), 4: (0, 0), 5: (0, 0), 6: (0, 0),
             8: (0, 0), 12: (0, 0), 16: (0, 0)},
    "oblong": {2: (1, 1), 3: (1, 2), 4: (3, 3), 5: (1, 1), 6: (4, 1),
               8: (7, 7), 12: (11, 3), 16: (15, 13)},
}


def _cases() -> dict[str, np.ndarray]:
    """Deterministic, and covering what varies: channels, shape, content."""
    rng = np.random.default_rng(0)
    out = {}
    block = rng.integers(0, 255, (24, 24, 3)).astype(np.uint8)
    out["lattice8"] = np.repeat(np.repeat(block, 8, axis=0), 8, axis=1)
    block = rng.integers(0, 255, (32, 32, 4)).astype(np.uint8)
    out["lattice6_rgba"] = np.repeat(np.repeat(block, 6, axis=0), 6, axis=1)
    out["noise_rgb"] = rng.integers(0, 255, (96, 96, 3)).astype(np.uint8)
    out["noise_rgba"] = rng.integers(0, 255, (96, 96, 4)).astype(np.uint8)
    ramp = np.linspace(0, 255, 120, dtype=np.float32)
    field = (ramp[None, :] * 0.6 + ramp[:, None] * 0.4)[..., None] * np.array(
        [1.0, 0.7, 0.4])
    out["gradient"] = np.clip(field + rng.normal(0, 8, (120, 120, 3)),
                              0, 255).astype(np.uint8)
    out["flat"] = np.full((64, 64, 3), 120, np.uint8)
    out["oblong"] = rng.integers(0, 255, (60, 140, 3)).astype(np.uint8)
    return out


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_measured_phase_is_pinned(name):
    image = _cases()[name]
    got = {f: find_phase(image, f) for f in EXPECTED[name]}
    assert got == EXPECTED[name]


def test_a_true_lattice_is_found_at_its_own_factor():
    """The measurement's whole purpose: art drawn in 8px blocks starts at 0,0
    and any other phase straddles a boundary."""
    rng = np.random.default_rng(0)
    block = rng.integers(0, 255, (24, 24, 3)).astype(np.uint8)
    assert find_phase(np.repeat(np.repeat(block, 8, axis=0), 8, axis=1), 8) == (0, 0)


def test_a_shifted_lattice_is_found_where_it_actually_starts():
    rng = np.random.default_rng(1)
    block = rng.integers(0, 255, (20, 20, 3)).astype(np.uint8)
    art = np.repeat(np.repeat(block, 8, axis=0), 8, axis=1)
    assert find_phase(art[3:, 5:], 8) == (3, 5)


def test_a_factor_of_one_has_only_one_phase():
    assert find_phase(np.zeros((8, 8, 3), np.uint8), 1) == (0, 0)


def test_a_factor_larger_than_the_image_is_refused_by_name():
    with pytest.raises(Invalid):
        find_phase(np.zeros((8, 8, 3), np.uint8), 9)


def test_the_answer_does_not_drift_between_calls():
    image = _cases()["noise_rgba"]
    assert find_phase(image, 8) == find_phase(image, 8)
