from __future__ import annotations

import pytest

from pipeline.orchestration import queue as q
from pipeline.shared import paths


@pytest.fixture
def queue(root, tmp_path):
    (tmp_path / "library" / "configs").mkdir(parents=True, exist_ok=True)
    for name in ("knight_attack", "character_sheet", "_global"):
        src = paths.resolve(root, "configs") / f"{name}.yaml"
        if src.exists():
            (tmp_path / "library" / "configs" / f"{name}.yaml").write_text(src.read_text())
    return q.Queue(tmp_path)


def test_a_matrix_expands_into_one_job_per_combination(queue):
    jobs = queue.submit({"config": "knight_attack",
                         "matrix": {"a": [1, 2, 3], "b": ["x", "y"]}})
    assert len(jobs) == 6
    cells = {tuple(sorted(j.data["matrix_cell"].items())) for j in jobs}
    assert len(cells) == 6, "matrix produced duplicate combinations"


@pytest.mark.parametrize("label,spec,verdict", [
    ("unknown config", {"config": "nope"}, "problems"),
    ("unknown rig", {"config": "knight_attack",
                     "overrides": {"rig": "griffin"}}, "problems"),
    ("input not there yet", {"config": "knight_attack",
                             "needs": ["missing.png"]}, "held"),
])
def test_preflight_tells_broken_from_not_ready_yet(queue, tmp_path, label, spec,
                                                   verdict):
    for f in queue.dir(q.PENDING).glob("*"):
        f.unlink()
    result = q.preflight(tmp_path, queue.submit(spec)[0])
    if verdict == "problems":
        assert result.problems, f"{label} should fail immediately"
    else:
        assert result.held, f"{label} should be held, not failed"


def test_a_dead_service_returns_a_verdict_instead_of_raising(root):
    # Every stage rejects a missing ComfyUI in about a millisecond, so a worker
    # that treated that as a job error would empty a 200-job queue into failed/
    # faster than a person could read one line of the log.
    ok, why = q.services_up(root)
    assert isinstance(ok, bool)
    if not ok:
        assert "unreachable" in why, "an outage should be described, not swallowed"
