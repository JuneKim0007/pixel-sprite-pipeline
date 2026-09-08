from __future__ import annotations

from pathlib import Path

from pipeline.refs.references import Reference, pick, unresolved

# Keywords, not positions: `role` was inserted second and positional construction silently made yaw="front".
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
    scaled = Reference(path=Path("a.png"), yaw=0, label="front", weight_scale=0.5)
    assert abs(0.85 * scaled.weight_scale - 0.425) < 1e-9


def test_unresolved_names_every_dead_path_not_only_the_first(tmp_path):
    """`load` raises on the first; a check that fixes one path per run is a check
    nobody finishes. Eighteen configs were broken this way at once."""
    (tmp_path / "library" / "refs").mkdir(parents=True)
    (tmp_path / "library" / "refs" / "here.png").write_bytes(b"x")

    cfg = {
        "identity": [
            {"path": "library/refs/here.png", "view": "front"},
            {"path": "overnight/char_3/refs/side.png", "view": "side"},
            {"path": "overnight/char_3/refs/rear.png", "view": "rear"},
        ],
        "palette": [{"path": "palettes/char_3.hex"}],
    }
    dead = unresolved(tmp_path, cfg)
    assert len(dead) == 3, dead
    assert dead[0].startswith("references.identity")
    assert dead[-1].startswith("references.palette")
    assert all("here.png" not in d for d in dead), "a resolvable path was reported"


def test_unresolved_accepts_a_bare_string_entry():
    """`load` allows `- path.png` as shorthand, so the check has to as well."""
    assert unresolved(Path("/nowhere"), {"style": ["gone.png"]}) == [
        "references.style: gone.png"]


def test_unresolved_is_quiet_when_there_is_nothing_to_check():
    assert unresolved(Path("/nowhere"), None) == []
    assert unresolved(Path("/nowhere"), {"identity": []}) == []
