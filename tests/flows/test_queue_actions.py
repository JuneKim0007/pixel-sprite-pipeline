"""What `queue_act` does to one job, and in what order it refuses.

Three refusals share this function — a running job, an unknown action, an
unknown job — and which one a caller sees depends on the order they are
checked in. That order is the part a restructure could quietly reverse, so it
is what these tests pin.
"""

from __future__ import annotations

import pytest

from pipeline.api import jobs as jobs_api
from pipeline.orchestration import queue as q
from pipeline.shared import errors, paths


@pytest.fixture
def queue(root, tmp_path, monkeypatch):
    (tmp_path / "library" / "configs").mkdir(parents=True, exist_ok=True)
    for name in ("knight_attack", "_global"):
        src = paths.resolve(root, "configs") / f"{name}.yaml"
        if src.exists():
            (tmp_path / "library" / "configs" / f"{name}.yaml").write_text(
                src.read_text())
    monkeypatch.setattr(jobs_api, "ROOT", tmp_path)
    return q.Queue(tmp_path)


def submit(queue, name="job", state=None):
    job = queue.submit({"config": "knight_attack", "name": name})[0]
    if state and state != q.PENDING:
        job = queue.move(job, state)
    return job


def test_retry_returns_a_failed_job_to_pending_and_clears_its_attempts(queue):
    job = submit(queue, "a", q.FAILED)
    queue.move(job, q.FAILED, attempts=2, error="boom")

    assert jobs_api.queue_act(job.id, "retry") == {
        "ok": True, "job": job.id, "action": "retry"}
    pending = queue.list(q.PENDING)
    assert [j.id for j in pending] == [job.id]
    assert pending[0].data["attempts"] == 0
    assert pending[0].data["error"] is None


def test_hold_moves_a_job_aside_with_a_time_to_come_back(queue):
    job = submit(queue, "a")

    jobs_api.queue_act(job.id, "hold")
    held = queue.list(q.HELD)
    assert [j.id for j in held] == [job.id]
    assert held[0].data["retry_after"] > 0


def test_drop_removes_the_job_entirely(queue):
    job = submit(queue, "a")

    jobs_api.queue_act(job.id, "drop")
    assert all(queue.list(state) == [] for state in q.STATES)
    assert not job.path.exists()


def test_a_running_job_is_refused_before_its_action_is_read(queue):
    """Conflict beats Invalid: the running check comes first, and must."""
    job = submit(queue, "a", q.RUNNING)

    with pytest.raises(errors.Conflict, match="is running"):
        jobs_api.queue_act(job.id, "retry")
    with pytest.raises(errors.Conflict, match="is running"):
        jobs_api.queue_act(job.id, "telekinesis")

    assert [j.id for j in queue.list(q.RUNNING)] == [job.id], "it did not move"


def test_an_unknown_action_on_a_real_job_names_the_ones_that_work(queue):
    job = submit(queue, "a")

    with pytest.raises(errors.Invalid) as caught:
        jobs_api.queue_act(job.id, "telekinesis")
    assert "telekinesis" in str(caught.value)
    assert [j.id for j in queue.list(q.PENDING)] == [job.id], "it did not move"


def test_an_unknown_job_is_not_found_whatever_the_action(queue):
    submit(queue, "a")

    with pytest.raises(errors.NotFound):
        jobs_api.queue_act("no_such_job", "retry")
    with pytest.raises(errors.NotFound):
        jobs_api.queue_act("no_such_job", "telekinesis")


def test_a_job_is_found_in_any_state_it_sits_in(queue):
    for state in (q.PENDING, q.DONE, q.FAILED, q.HELD):
        job = submit(queue, f"in_{state}", state)
        assert jobs_api.queue_act(job.id, "hold")["ok"], state
        for j in queue.list(q.HELD):
            jobs_api.queue_act(j.id, "drop")


def test_only_the_named_job_is_touched(queue):
    keep = submit(queue, "a_keep")
    target = submit(queue, "b_target")

    jobs_api.queue_act(target.id, "drop")
    assert [j.id for j in queue.list(q.PENDING)] == [keep.id]
