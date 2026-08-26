"""The watchdog that kills a process instead of letting it kill the session."""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from pipeline.shared import limits
from pipeline.shared.guard import (PRESSURE_CRITICAL, PRESSURE_NORMAL, Guard,
                                   adopt_pidfiles)


@pytest.fixture
def victim():
    """A real process the guard is allowed to kill."""
    proc = subprocess.Popen(["/bin/sleep", "30"])
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
    guard = Guard()
    guard.watch(victim.pid, "victim", expected_large=True)
    monkeypatch.setattr(guard, "pressure", lambda: PRESSURE_CRITICAL)
    for _ in range(6):
        guard.check()
    assert _settled(victim), "survived sustained critical memory pressure"
    assert "pressure critical" in guard.kills[0]["why"]


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
