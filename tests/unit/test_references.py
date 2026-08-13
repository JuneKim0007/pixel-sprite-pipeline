from __future__ import annotations

from pathlib import Path

from pipeline.refs.references import Reference, pick

# Keywords, not positions: `role` was inserted second and positional
# construction silently made yaw="front".
REFS = [Reference(path=Path("front.png"), yaw=0, label="front"),
        Reference(path=Path("rear.png"), yaw=180, label="rear")]


def test_weight_falls_off_with_angular_distance():
    _, near_w, near_d = pick(REFS, 0, tolerance=40)
    _, far_w, far_d = pick(REFS[:1], 180, tolerance=40)
    assert near_d == 0, "an exact match was not recognised"
    assert far_d == 180, "a full mismatch was not measured"
    assert far_w < near_w


def test_the_nearer_reference_is_chosen():
    assert pick(REFS, 170, tolerance=40)[0].label == "rear"


def test_a_per_image_weight_scales_the_result():
    scaled = Reference(path=Path("a.png"), yaw=0, label="front", weight_scale=0.5)
    _, full, _ = pick([REFS[0]], 0, tolerance=40, exact_weight=0.8)
    _, half, _ = pick([scaled], 0, tolerance=40, exact_weight=0.8)
    assert abs(full - 0.8) < 1e-9
    assert abs(half - 0.4) < 1e-9


def test_the_manual_branch_applies_the_scale_too():
    # As frames.py computes it.
    scaled = Reference(path=Path("a.png"), yaw=0, label="front", weight_scale=0.5)
    assert abs(0.85 * scaled.weight_scale - 0.425) < 1e-9
