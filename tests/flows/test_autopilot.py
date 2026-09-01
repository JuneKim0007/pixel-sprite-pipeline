"""What the autopilot loop decides, with the subprocess and the network faked.

`work` is the unattended runner: it takes the next ready job, asks whether it
could run at all, waits for the services, runs it, and decides what a failure
means. Everything but `run_job` and `services_up` is real here — the queue is
the filesystem-backed one and preflight is the real preflight — so what is
under test is the loop's own judgement rather than a mock's.
"""

from __future__ import annotations

import argparse

import pytest

import autopilot
from pipeline.orchestration import queue as q
from pipeline.shared import paths


@pytest.fixture
def home(root, tmp_path, monkeypatch):
    """A queue root with real configs, wired so autopilot works against it."""
    (tmp_path / "library" / "configs").mkdir(parents=True, exist_ok=True)
    for name in ("knight_attack", "character_sheet", "_global"):
        src = paths.resolve(root, "configs") / f"{name}.yaml"
        if src.exists():
            (tmp_path / "library" / "configs" / f"{name}.yaml").write_text(
                src.read_text())
    monkeypatch.setattr(autopilot, "ROOT", tmp_path)
    monkeypatch.setattr(autopilot, "STOPPING", False)
    monkeypatch.setattr(q, "services_up", lambda r, need_llm=False: (True, ""))

    # Bounded rather than a no-op: without --drain an empty queue polls forever,
    # so an unexpectedly spinning loop must fail the test instead of hanging it.
    waits = []

    def _sleep(_seconds):
        waits.append(_seconds)
        if len(waits) > 50:
            raise AssertionError(f"work() slept {len(waits)} times without progress")

    monkeypatch.setattr(autopilot.time, "sleep", _sleep)
    return tmp_path


@pytest.fixture
def queue(home):
    return q.Queue(home)


def opts(**over):
    base = dict(drain=True, poll=0, hold=600, service_wait=0, timeout=60,
                retries=2, breaker=5, once=False)
    base.update(over)
    return argparse.Namespace(**base)


def runs(monkeypatch, *results):
    """Make run_job answer with each result in turn, recording the job ids."""
    seen = []
    answers = list(results)

    def fake(root, job, timeout):
        seen.append(job.id)
        return answers.pop(0) if answers else (True, "rid", "")

    monkeypatch.setattr(autopilot, "run_job", fake)
    return seen


def test_a_good_job_runs_once_and_lands_in_done(queue, monkeypatch):
    seen = runs(monkeypatch, (True, "rid", ""))
    queue.submit({"config": "knight_attack"})

    assert autopilot.work(opts()) == 0
    assert len(seen) == 1
    assert [j.id for j in queue.list(q.DONE)] == seen
    assert queue.list(q.PENDING) == []


def test_drain_exits_when_the_queue_empties(queue, monkeypatch):
    runs(monkeypatch)
    assert autopilot.work(opts(drain=True)) == 0


def test_once_stops_after_a_single_success(queue, monkeypatch):
    seen = runs(monkeypatch, (True, "a", ""), (True, "b", ""))
    queue.submit({"config": "knight_attack", "name": "a_first"})
    queue.submit({"config": "knight_attack", "name": "b_second"})

    assert autopilot.work(opts(once=True, drain=False)) == 0
    assert len(seen) == 1, "--once must not take a second job"
    assert len(queue.list(q.PENDING)) == 1


def test_a_job_that_cannot_run_is_failed_without_being_started(queue, monkeypatch):
    seen = runs(monkeypatch)
    queue.submit({"config": "nope"})

    assert autopilot.work(opts()) == 0
    assert seen == [], "preflight must reject it before run_job"
    failed = queue.list(q.FAILED)
    assert len(failed) == 1
    assert "nope" in (failed[0].data.get("error") or "")


def test_a_failing_preflight_writes_the_reason_beside_the_job(queue, monkeypatch):
    runs(monkeypatch)
    queue.submit({"config": "nope"})
    autopilot.work(opts())

    errors = list(queue.dir(q.FAILED).glob("*.error.txt"))
    assert len(errors) == 1
    assert "nope" in errors[0].read_text()


def test_a_job_waiting_on_a_dependency_is_held_not_failed(queue, monkeypatch):
    """--drain does not exit while anything is held, so this stops on a signal.

    A held job may still become ready, so an empty pending queue with a held
    job is not a drained queue.
    """
    runs(monkeypatch)
    monkeypatch.setattr(autopilot.time, "sleep",
                        lambda _s: setattr(autopilot, "STOPPING", True))
    queue.submit({"config": "knight_attack", "needs": ["missing.png"]})

    assert autopilot.work(opts()) == 0
    assert queue.list(q.FAILED) == []
    held = queue.list(q.HELD)
    assert len(held) == 1
    assert "missing.png" in (held[0].data.get("error") or "")
    assert held[0].data.get("retry_after"), "a held job must say when to retry"


def test_a_first_failure_is_retried(queue, monkeypatch):
    seen = runs(monkeypatch, (False, "rid", "boom"), (True, "rid", ""))
    queue.submit({"config": "knight_attack"})

    assert autopilot.work(opts(retries=2)) == 0
    assert len(seen) == 2, "the job is taken a second time"
    assert len(queue.list(q.DONE)) == 1


def test_a_second_failure_is_final(queue, monkeypatch):
    seen = runs(monkeypatch, (False, "rid", "boom"), (False, "rid", "boom again"))
    queue.submit({"config": "knight_attack"})

    assert autopilot.work(opts(retries=2)) == 0
    assert len(seen) == 2
    failed = queue.list(q.FAILED)
    assert len(failed) == 1
    assert failed[0].data["attempts"] == 2


def test_retries_one_means_no_retry_at_all(queue, monkeypatch):
    seen = runs(monkeypatch, (False, "rid", "boom"))
    queue.submit({"config": "knight_attack"})

    autopilot.work(opts(retries=1))
    assert len(seen) == 1
    assert len(queue.list(q.FAILED)) == 1


def test_the_breaker_stops_the_run_after_consecutive_failures(queue, monkeypatch):
    runs(monkeypatch)
    for i in range(3):
        queue.submit({"config": "nope", "name": f"bad_{i}"})

    assert autopilot.work(opts(breaker=2)) == 1, "a tripped breaker exits non-zero"
    assert len(queue.list(q.FAILED)) == 2, "it stops at the breaker, not the end"


def test_a_success_resets_the_breaker(queue, monkeypatch):
    runs(monkeypatch, (True, "rid", ""))
    queue.submit({"config": "nope", "name": "a_bad"})
    queue.submit({"config": "knight_attack", "name": "b_good"})
    queue.submit({"config": "nope", "name": "c_bad"})

    assert autopilot.work(opts(breaker=2)) == 0, \
        "two failures either side of a success are not consecutive"
    assert len(queue.list(q.FAILED)) == 2


def test_a_finished_job_queues_what_it_chains_to(queue, monkeypatch):
    runs(monkeypatch, (True, "rid", ""))
    queue.submit({"config": "knight_attack",
                  "then": [{"config": "character_sheet"}]})

    assert autopilot.work(opts(once=True, drain=False)) == 0
    pending = queue.list(q.PENDING)
    assert len(pending) == 1
    assert pending[0].config == "character_sheet"
    assert pending[0].data["overrides"]["references.from_run"] == "rid", \
        "a chained job inherits the run it followed"


def test_a_chained_job_can_decline_to_inherit(queue, monkeypatch):
    runs(monkeypatch, (True, "rid", ""))
    queue.submit({"config": "knight_attack",
                  "then": [{"config": "character_sheet", "inherit": False}]})
    autopilot.work(opts(once=True, drain=False))

    pending = queue.list(q.PENDING)[0]
    assert "references.from_run" not in (pending.data.get("overrides") or {})


def test_a_job_left_running_by_a_crash_is_reclaimed(queue, monkeypatch):
    seen = runs(monkeypatch, (True, "rid", ""))
    job = queue.submit({"config": "knight_attack"})[0]
    queue.move(job, q.RUNNING)
    assert queue.list(q.PENDING) == []

    assert autopilot.work(opts()) == 0
    assert len(seen) == 1, "the reclaimed job is picked up and run"


def test_the_loop_waits_while_the_services_are_down(queue, monkeypatch):
    seen = runs(monkeypatch, (True, "rid", ""))
    answers = [(False, "ComfyUI unreachable"), (False, "still down"), (True, "")]
    monkeypatch.setattr(q, "services_up",
                        lambda r, need_llm=False: answers.pop(0) if answers
                        else (True, ""))
    queue.submit({"config": "knight_attack"})

    assert autopilot.work(opts()) == 0
    assert len(seen) == 1, "it runs the job once the services come back"


def test_stopping_ends_the_loop_without_running_anything(queue, monkeypatch):
    seen = runs(monkeypatch)
    monkeypatch.setattr(autopilot, "STOPPING", True)
    queue.submit({"config": "knight_attack"})

    assert autopilot.work(opts()) == 0
    assert seen == []


def test_the_breaker_also_trips_on_repeated_run_failures(queue, monkeypatch):
    """The breaker is written twice — once for preflight, once for the run.

    Every other breaker test here fails at preflight and exercises only the
    first arm.
    """
    runs(monkeypatch, (False, "r", "boom"), (False, "r", "boom"),
         (False, "r", "boom"))
    for name in ("a_bad", "b_bad", "c_bad"):
        queue.submit({"config": "knight_attack", "name": name})

    assert autopilot.work(opts(breaker=2, retries=1)) == 1
    assert len(queue.list(q.FAILED)) == 2, "it stops at the breaker"
    assert len(queue.list(q.PENDING)) == 1, "the third job is never taken"
