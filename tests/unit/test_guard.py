"""The watchdog that kills a process instead of letting it kill the session."""

from __future__ import annotations

import os
import signal
import subprocess
import time

import pytest

from pipeline.shared import limits
from pipeline.shared.guard import (PRESSURE_CRITICAL, PRESSURE_NORMAL, Guard,
                                   adopt_pidfiles)


@pytest.fixture
def victim():
    """A real process the guard is allowed to kill, in its own process group.

    `_signal` kills the group, because the memory a reading measured is usually
    in a worker rather than the leader, and ctl.sh starts every service in its
    own group for exactly that reason. A victim sharing pytest's group takes
    the test runner down with it - measured as exit 137 partway through the
    suite.
    """
    proc = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


@pytest.fixture
def tiny_ceiling():
    """A per-process ceiling below anything, so any process is over it."""
    before = limits._STATE["rss_share"]
    limits._STATE["rss_share"] = 1e-9
    yield
    limits._STATE["rss_share"] = before


def _settled(proc, timeout=3.0):
    """Whether the process is gone, without racing the kernel's bookkeeping."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


def test_pressure_reads_the_machine_s_own_opinion():
    assert Guard().pressure() in (PRESSURE_NORMAL, 2, PRESSURE_CRITICAL)


def test_unknown_pressure_is_not_an_emergency(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert Guard().pressure() == PRESSURE_NORMAL


def test_rss_reads_several_processes_in_one_call(victim):
    usage = Guard().rss([os.getpid(), victim.pid])
    assert set(usage) == {os.getpid(), victim.pid}
    assert all(v > 0 for v in usage.values())


def test_rss_of_nothing_asks_nothing():
    assert Guard().rss([]) == {}


def test_one_reading_over_the_ceiling_is_not_enough(victim, tiny_ceiling):
    guard = Guard()
    guard.watch(victim.pid, "victim")
    guard.check()
    assert victim.poll() is None, "killed on the first reading"
    assert guard.kills == []


def test_two_readings_over_the_ceiling_kills(victim, tiny_ceiling):
    guard = Guard()
    guard.watch(victim.pid, "victim")
    guard.check()
    guard.check()
    assert _settled(victim), "still alive after two readings over the ceiling"
    assert guard.kills[0]["name"] == "victim"
    assert "per-process limit" in guard.kills[0]["why"]


def test_a_process_that_comes_back_under_loses_its_strike(victim):
    guard = Guard()
    guard.watch(victim.pid, "victim")
    limits._STATE["rss_share"] = 1e-9
    guard.check()
    limits._STATE["rss_share"] = 0.35
    guard.check()
    limits._STATE["rss_share"] = 1e-9
    guard.check()
    limits._STATE["rss_share"] = 0.35
    assert victim.poll() is None, "strikes accumulated across a recovery"


def test_an_expected_large_process_is_exempt_from_the_per_process_cap(
        victim, tiny_ceiling):
    guard = Guard()
    guard.watch(victim.pid, "comfy", expected_large=True)
    guard.check()
    guard.check()
    assert victim.poll() is None, "killed a process that is meant to be large"
    assert guard.kills == []


def test_sustained_critical_pressure_kills_the_largest(monkeypatch, victim):
    """A /bin/sleep is a few MB, so its size is faked to make it a candidate.

    The floor exists because killing a small process frees nothing: measured
    2026-09-10, the guard killed comfy at 0.05 GB and then the UI at 0.02 GB,
    which is the process it runs inside.
    """
    guard = Guard()
    guard.watch(victim.pid, "victim", expected_large=True)
    monkeypatch.setattr(guard, "pressure", lambda: PRESSURE_CRITICAL)
    monkeypatch.setattr(Guard, "rss",
                        staticmethod(lambda pids: {p: 4 << 30 for p in pids}))
    for _ in range(6):
        guard.check()
    assert _settled(victim), "survived sustained critical memory pressure"
    assert "pressure critical" in guard.kills[0]["why"]


def test_a_small_process_is_not_what_the_machine_is_short_of(monkeypatch, victim):
    from pipeline.shared.guard import PRESSURE_FLOOR

    guard = Guard()
    guard.watch(victim.pid, "victim", expected_large=True)
    monkeypatch.setattr(guard, "pressure", lambda: PRESSURE_CRITICAL)
    monkeypatch.setattr(Guard, "rss",
                        staticmethod(lambda pids: {p: PRESSURE_FLOOR // 20 for p in pids}))
    for _ in range(6):
        guard.check()
    assert victim.poll() is None, "killed a process too small to be the cause"
    assert guard.kills == []


def test_the_guard_does_not_kill_the_process_it_runs_in(monkeypatch):
    """It killed the UI at 0.02 GB, and then could not guard anything."""
    guard = Guard()
    guard.watch(os.getpid(), "ui")
    monkeypatch.setattr(guard, "pressure", lambda: PRESSURE_CRITICAL)
    monkeypatch.setattr(Guard, "rss",
                        staticmethod(lambda pids: {p: 8 << 30 for p in pids}))
    for _ in range(6):
        guard.check()
    assert guard.kills == [], "the guard killed its own host"


def test_a_pressure_spike_is_not_sustained_pressure(monkeypatch, victim):
    guard = Guard()
    guard.watch(victim.pid, "victim", expected_large=True)
    levels = iter([PRESSURE_CRITICAL, PRESSURE_CRITICAL, PRESSURE_NORMAL,
                   PRESSURE_CRITICAL, PRESSURE_NORMAL])
    monkeypatch.setattr(guard, "pressure", lambda: next(levels))
    for _ in range(5):
        guard.check()
    assert victim.poll() is None, "killed on a spike rather than a trend"


def test_an_exited_process_is_forgotten_not_re_killed(victim):
    guard = Guard()
    guard.watch(victim.pid, "victim")
    victim.kill()
    victim.wait(timeout=5)
    guard.check()
    assert guard.watched == {}
    assert guard.kills == []


def test_killing_a_process_that_already_exited_is_not_an_error(victim,
                                                               tiny_ceiling):
    guard = Guard()
    guard.watch(victim.pid, "victim")
    guard.check()
    victim.kill()
    victim.wait(timeout=5)
    guard.check()
    assert guard.watched == {}


def test_a_bug_in_check_does_not_kill_the_guard(monkeypatch):
    from pipeline.shared import guard as guard_mod

    guard = Guard()
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("sensor exploded")

    monkeypatch.setattr(guard, "check", boom)
    # At the real one-second interval this one assertion cost 2.5s, which was half the runtime of the whole suite.
    monkeypatch.setattr(guard_mod, "INTERVAL_S", 0.01)
    guard.start()
    deadline = time.monotonic() + 2.0
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    guard.stop()
    assert len(calls) >= 2, "the loop stopped at the first exception"


def test_adopt_reads_the_pidfiles_ctl_sh_writes(tmp_path, monkeypatch):
    from pipeline.shared import guard as guard_mod

    (tmp_path / "comfy.pid").write_text(f"{os.getpid()}\n")
    (tmp_path / "stale.pid").write_text("999999999")
    (tmp_path / "junk.pid").write_text("not a number")

    monkeypatch.setattr(guard_mod, "GUARD", Guard())
    adopted = adopt_pidfiles(tmp_path)

    assert adopted == [f"comfy:{os.getpid()}"]
    assert guard_mod.GUARD.watched[os.getpid()].expected_large is True


def test_a_failed_reading_does_not_disarm_the_guard(monkeypatch, victim):
    """`ps` failing means no information, not that every process exited.

    Spawning `ps` is what fails first under the memory pressure this guard
    exists to survive, so a failed reading that forgot every watched process
    would disarm the guard at exactly the moment it is needed.
    """
    guard = Guard()
    guard.watch(victim.pid, "victim")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))

    guard.check()

    assert victim.pid in guard.watched, "one failed ps call disarmed the guard"
    assert guard.kills == []


@pytest.fixture
def family():
    """A leader with a worker under it, in its own group as ctl.sh starts one."""
    proc = subprocess.Popen(["/bin/sh", "-c", "/bin/sleep 30 & wait"],
                            start_new_session=True)
    deadline = time.time() + 3.0
    while time.time() < deadline and not _children(proc.pid):
        time.sleep(0.05)
    yield proc
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        if proc.poll() is None:
            proc.kill()
    proc.wait(timeout=5)


def _children(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    return [int(p) for p in out.stdout.split()]


def test_a_worker_s_memory_counts_against_the_service_that_spawned_it(family):
    """`ollama serve` is 15 MB whatever model is loaded; the runner holds it."""
    worker = _children(family.pid)
    assert worker, "the fixture never spawned a child"

    whole = Guard().rss([family.pid])
    leader_only = sum(Guard().rss([family.pid, w])[w] for w in worker)

    assert whole[family.pid] > leader_only, "the worker was not counted"


def test_a_worker_watched_in_its_own_right_is_not_counted_twice(family):
    worker = _children(family.pid)
    assert worker, "the fixture never spawned a child"

    tree = Guard().rss([family.pid])[family.pid]
    split = Guard().rss([family.pid, *worker])

    assert split[family.pid] < tree, "the worker was counted on both readings"
    assert all(split[w] > 0 for w in worker)


def test_a_model_server_is_adopted_as_expected_to_be_large(tmp_path, monkeypatch):
    from pipeline.shared import guard as guard_mod

    (tmp_path / "ollama.pid").write_text(f"{os.getpid()}\n")
    monkeypatch.setattr(guard_mod, "GUARD", Guard())

    adopt_pidfiles(tmp_path)

    assert guard_mod.GUARD.watched[os.getpid()].expected_large is True


def test_a_live_run_is_found_by_its_command_line(monkeypatch):
    """Whoever started it may be gone: the UI restarts, the autopilot is separate."""
    from pipeline.shared import guard as guard_mod

    class Out:
        stdout = ("/usr/bin/python -u /x/run.py /x/cfg.yaml --run-id 20260101_000000_a\n"
                  "/bin/zsh -c something else\n")

    monkeypatch.setattr(guard_mod.subprocess, "run", lambda *a, **k: Out())
    assert guard_mod.run_in_flight() == "20260101_000000_a"


def test_nothing_running_is_not_a_run(monkeypatch):
    from pipeline.shared import guard as guard_mod

    class Out:
        stdout = "/bin/zsh -c nothing to do here\n"

    monkeypatch.setattr(guard_mod.subprocess, "run", lambda *a, **k: Out())
    assert guard_mod.run_in_flight() is None


def test_a_run_py_without_a_run_id_is_ignored(monkeypatch):
    """--list and --help both invoke run.py and start nothing."""
    from pipeline.shared import guard as guard_mod

    class Out:
        stdout = "/usr/bin/python /x/run.py --list\n"

    monkeypatch.setattr(guard_mod.subprocess, "run", lambda *a, **k: Out())
    assert guard_mod.run_in_flight() is None


def test_ps_failing_says_nothing_rather_than_no(monkeypatch):
    from pipeline.shared import guard as guard_mod

    def boom(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(guard_mod.subprocess, "run", boom)
    assert guard_mod.run_in_flight() is None
