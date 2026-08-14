"""The watchdog that kills a process instead of letting it kill the session.

Everything else in this codebase bounds work a process asks of itself. This
bounds a process from outside, because the thing that failed on 2026-08-13 was
not this process - it was WindowServer, starved into missing its watchdog
check-ins, after which macOS panicked the kernel on purpose. A limit inside the
allocating process could not have caught that: by the time an allocation is
observable it has happened, and the process that dies is not the one at fault.

These tests spawn real `sleep` processes and really kill them. Mocking the
kill would test the arithmetic and not the thing that has to work.
"""

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


# ------------------------------------------------------------- the signals


def test_pressure_reads_the_machine_s_own_opinion():
    # The same subsystem jetsam uses, rather than this program's guess at how
    # close the machine is. 1 normal, 2 warning, 4 critical.
    assert Guard().pressure() in (PRESSURE_NORMAL, 2, PRESSURE_CRITICAL)


def test_unknown_pressure_is_not_an_emergency(monkeypatch):
    # A guard that kills things when it cannot read the sensor is worse than
    # no guard.
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert Guard().pressure() == PRESSURE_NORMAL


def test_rss_reads_several_processes_in_one_call(victim):
    usage = Guard().rss([os.getpid(), victim.pid])
    assert set(usage) == {os.getpid(), victim.pid}
    assert all(v > 0 for v in usage.values())


def test_rss_of_nothing_asks_nothing():
    assert Guard().rss([]) == {}


# -------------------------------------------------------- the per-process cap


def test_one_reading_over_the_ceiling_is_not_enough(victim, tiny_ceiling):
    # ps can catch a process mid-spike. Acting on a single sample would kill
    # renders for transients that resolve themselves.
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
    # Strikes must not accumulate across unrelated spikes hours apart.
    guard = Guard()
    guard.watch(victim.pid, "victim")
    limits._STATE["rss_share"] = 1e-9
    guard.check()                                   # one strike
    limits._STATE["rss_share"] = 0.35
    guard.check()                                   # back under: cleared
    limits._STATE["rss_share"] = 1e-9
    guard.check()                                   # this is strike one again
    limits._STATE["rss_share"] = 0.35
    assert victim.poll() is None, "strikes accumulated across a recovery"


def test_an_expected_large_process_is_exempt_from_the_per_process_cap(
        victim, tiny_ceiling):
    # ComfyUI holds a diffusion model resident by design, so its size is not by
    # itself evidence of anything. Only system-wide pressure takes it.
    guard = Guard()
    guard.watch(victim.pid, "comfy", expected_large=True)
    guard.check()
    guard.check()
    assert victim.poll() is None, "killed a process that is meant to be large"
    assert guard.kills == []


# ----------------------------------------------------- system-wide pressure


def test_sustained_critical_pressure_kills_the_largest(monkeypatch, victim):
    # The case a per-process limit cannot see: nobody is over their own limit
    # and the machine is going down anyway. The largest goes, because it frees
    # the most and the session is worth more than any one job.
    guard = Guard()
    guard.watch(victim.pid, "victim", expected_large=True)   # exempt per-process
    monkeypatch.setattr(guard, "pressure", lambda: PRESSURE_CRITICAL)
    for _ in range(6):
        guard.check()
    assert _settled(victim), "survived sustained critical memory pressure"
    assert "pressure critical" in guard.kills[0]["why"]


def test_a_pressure_spike_is_not_sustained_pressure(monkeypatch, victim):
    # Pressure spikes during ordinary things - a file copy, a tab opening.
    # Killing a render for one is a worse bug than the one this fixes.
    guard = Guard()
    guard.watch(victim.pid, "victim", expected_large=True)
    levels = iter([PRESSURE_CRITICAL, PRESSURE_CRITICAL, PRESSURE_NORMAL,
                   PRESSURE_CRITICAL, PRESSURE_NORMAL])
    monkeypatch.setattr(guard, "pressure", lambda: next(levels))
    for _ in range(5):
        guard.check()
    assert victim.poll() is None, "killed on a spike rather than a trend"


# ------------------------------------------------------------- bookkeeping


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
    guard.check()                                   # strike one
    victim.kill()
    victim.wait(timeout=5)
    guard.check()                                   # it is gone; must not raise
    assert guard.watched == {}


def test_a_bug_in_check_does_not_kill_the_guard(monkeypatch):
    # The reason to have a guard is still true after the guard hits a bug.
    guard = Guard()
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("sensor exploded")

    monkeypatch.setattr(guard, "check", boom)
    guard.start()
    time.sleep(2.5)
    guard.stop()
    assert len(calls) >= 2, "the loop stopped at the first exception"


def test_adopt_reads_the_pidfiles_ctl_sh_writes(tmp_path, monkeypatch):
    from pipeline.shared import guard as guard_mod

    (tmp_path / "comfy.pid").write_text(f"{os.getpid()}\n")
    (tmp_path / "stale.pid").write_text("999999999")      # long gone
    (tmp_path / "junk.pid").write_text("not a number")

    monkeypatch.setattr(guard_mod, "GUARD", Guard())
    adopted = adopt_pidfiles(tmp_path)

    assert adopted == [f"comfy:{os.getpid()}"]
    # ComfyUI is expected to be large; discovering it must not mean capping it.
    assert guard_mod.GUARD.watched[os.getpid()].expected_large is True
