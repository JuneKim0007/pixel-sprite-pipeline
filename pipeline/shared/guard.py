"""Killing a process before it takes the machine with it.

On 2026-08-13 this laptop rebooted four times. The panic log says why, and it
is not what it looks like:

    panic(cpu 0): userspace watchdog timeout: no successful checkins from
    WindowServer (2 induced crashes) in 120 seconds

macOS rebooted on purpose. `watchdogd` requires WindowServer to check in; a
python3.12 holding 14.94 GB of a 16 GB machine left it unable to, so watchdogd
killed and restarted it twice, then panicked the kernel by design. Nothing in
this program crashed. Nothing in it could have caught anything - the process
that caused it was never the process that died.

That is the whole argument for this module. Every limit in shared/limits.py is
something a process asks of itself, and self-restraint has three failure modes
here, all of which happened:

  - it is too late. By the time an allocation is observable it has happened,
    and the pages are already gone from the machine.
  - it is not universal. The generation subprocess and ComfyUI are separate
    programs with their own allocators; limits.py says so and leaves them
    alone. They are the ones that reach 15 GB.
  - it does not measure the thing that fails. What fails is the machine, not
    the process. A process obeying a per-process limit perfectly still helps
    starve WindowServer if four of them do it at once.

So this watches from outside, on both signals, and its only action is SIGKILL:

    per-process    resident memory over limits.rss_share of RAM
    system-wide    kern.memorystatus_vm_pressure_level at critical

The second is the one that matters and the one a process cannot compute about
itself. It is the same subsystem jetsam uses, so it is the machine's own
opinion of how close it is rather than this program's guess.

Killing is the correct response and a deliberately blunt one. The alternative
to a killed render is not a completed render; it is a reboot that loses every
unsaved thing on the desktop, and a job that dies with an error in the log can
simply be run again.

What this is NOT: a scheduler, a throttle, or a way to run work that does not
fit. It has no opinion about performance. It draws one line, and the line is
"this process is now a danger to the session".

macOS has no cgroups, and RLIMIT_AS is unreliable under Metal's mappings -
MPS reserves address space far beyond its resident set, so a limit low enough
to matter refuses allocations that would have been fine. Polling RSS from
outside is cruder and actually works, which is the trade this makes.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

from . import limits

log = logging.getLogger("pixel.guard")

# How often to look. Memory pressure is a slow signal - the machine took tens
# of seconds to die each time - so a second is ample and costs one sysctl.
INTERVAL_S = 1.0

# kern.memorystatus_vm_pressure_level: 1 normal, 2 warning, 4 critical.
PRESSURE_NORMAL, PRESSURE_WARN, PRESSURE_CRITICAL = 1, 2, 4

# Consecutive critical readings before acting. Pressure spikes briefly during
# ordinary things - a large file copy, a browser tab opening - and killing a
# render for a spike that resolves itself is a worse bug than the one this
# fixes. Sustained is the signal; instantaneous is noise.
CRITICAL_TICKS = 5


@dataclass
class Watched:
    """A process this guard may kill, and why it is allowed to."""

    pid: int
    name: str
    # A process that is expected to be large. ComfyUI holds a diffusion model
    # resident by design, so the per-process limit does not apply to it and
    # only system-wide pressure will take it - at which point being large is
    # exactly what makes it the right one to kill.
    expected_large: bool = False
    on_kill: object = None
    strikes: int = 0


@dataclass
class Guard:
    watched: dict[int, Watched] = field(default_factory=dict)
    critical_streak: int = 0
    kills: list[dict] = field(default_factory=list)
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------ registration

    def watch(self, pid: int, name: str, *, expected_large: bool = False,
              on_kill=None) -> None:
        with self._lock:
            self.watched[pid] = Watched(pid, name, expected_large, on_kill)

    def forget(self, pid: int) -> None:
        with self._lock:
            self.watched.pop(pid, None)

    # ------------------------------------------------------------- the signals

    @staticmethod
    def pressure() -> int:
        """The machine's own view of how close it is. 1 normal, 4 critical."""
        try:
            out = subprocess.run(
                ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                capture_output=True, text=True, timeout=2)
            return int(out.stdout.strip() or PRESSURE_NORMAL)
        except (OSError, ValueError, subprocess.SubprocessError):
            return PRESSURE_NORMAL      # unknown is not an emergency

    @staticmethod
    def rss(pids: list[int]) -> dict[int, int]:
        """Resident bytes per pid, in one call rather than one call each."""
        if not pids:
            return {}
        try:
            out = subprocess.run(
                ["ps", "-o", "pid=,rss=", "-p", ",".join(str(p) for p in pids)],
                capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return {}
        found: dict[int, int] = {}
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:
                try:
                    found[int(parts[0])] = int(parts[1]) * 1024   # ps reports KB
                except ValueError:
                    pass
        return found

    # -------------------------------------------------------------- the action

    def _kill(self, target: Watched, why: str, rss: int) -> None:
        log.error("guard: killing %s (pid %d, %.2f GB) - %s",
                  target.name, target.pid, rss / (1 << 30), why)
        self.kills.append({"pid": target.pid, "name": target.name,
                           "rss": rss, "why": why, "at": time.time()})
        try:
            os.kill(target.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass                                  # it exited on its own; fine
        except PermissionError:
            log.error("guard: not permitted to kill pid %d", target.pid)
            return
        self.forget(target.pid)
        if callable(target.on_kill):
            try:
                target.on_kill(target)
            except Exception:                      # noqa: BLE001
                log.exception("guard: on_kill for %s raised", target.name)

    def check(self) -> dict:
        """One pass. Separated from the loop so a test can run it directly."""
        with self._lock:
            targets = list(self.watched.values())
        alive = {t.pid: t for t in targets}
        usage = self.rss(list(alive))

        # Anything ps did not report has exited.
        for pid in set(alive) - set(usage):
            self.forget(pid)

        ceiling = limits.get("rss_bytes")
        level = self.pressure()
        self.critical_streak = (self.critical_streak + 1
                                if level >= PRESSURE_CRITICAL else 0)

        for pid, rss in usage.items():
            target = alive[pid]
            if target.expected_large or rss <= ceiling:
                target.strikes = 0
                continue
            # Two readings, because ps can catch a process mid-spike and one
            # sample is not a trend.
            target.strikes += 1
            if target.strikes >= 2:
                self._kill(target, f"resident {rss / (1<<30):.2f} GB over the "
                                   f"{ceiling / (1<<30):.2f} GB per-process limit",
                           rss)

        # System-wide, and the reason this module exists. Nobody is over their
        # own limit and the machine is still going down, so the largest watched
        # process goes - it is the one whose death frees the most, and the
        # session is worth more than any single job.
        if self.critical_streak >= CRITICAL_TICKS and usage:
            with self._lock:
                still = {p: t for p, t in self.watched.items() if p in usage}
            if still:
                pid = max(still, key=lambda p: usage[p])
                self._kill(still[pid], f"system memory pressure critical for "
                                       f"{self.critical_streak}s", usage[pid])
                self.critical_streak = 0

        return {"pressure": level, "critical_streak": self.critical_streak,
                "watched": {t.name: usage.get(t.pid, 0) for t in targets},
                "ceiling": ceiling}

    # ---------------------------------------------------------------- the loop

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(INTERVAL_S):
                try:
                    self.check()
                except Exception:                  # noqa: BLE001
                    # A guard that dies of its own bug is worse than no guard,
                    # because the reason to have one is still true afterwards.
                    log.exception("guard: check raised; continuing")

        self._thread = threading.Thread(target=loop, name="guard", daemon=True)
        self._thread.start()
        log.info("guard: watching, per-process ceiling %.2f GB",
                 limits.get("rss_bytes") / (1 << 30))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=INTERVAL_S * 2)
            self._thread = None

    def describe(self) -> dict:
        with self._lock:
            watched = [{"pid": t.pid, "name": t.name,
                        "expected_large": t.expected_large}
                       for t in self.watched.values()]
        return {"running": self._thread is not None,
                "ceiling_bytes": limits.get("rss_bytes"),
                "pressure": self.pressure(),
                "watched": watched,
                "kills": self.kills[-10:]}


GUARD = Guard()


# Services scripts/ctl.sh starts and writes a pidfile for, and whether each is
# expected to be large. ComfyUI holds a diffusion model resident by design, so
# its size is not by itself evidence of anything; only system-wide pressure
# takes it, and then it goes first precisely because it is the biggest.
EXPECTED_LARGE = {"comfy"}


def adopt_pidfiles(run_dir) -> list[str]:
    """Watch whatever ctl.sh has running, by reading .run/*.pid.

    Discovered rather than spawned, because the server does not start these -
    `make up` does, and the two outlive each other in both directions. Re-read
    on every call so a service restarted underneath is picked up.
    """
    from pathlib import Path

    adopted = []
    for pidfile in sorted(Path(run_dir).glob("*.pid")):
        name = pidfile.stem
        try:
            pid = int(pidfile.read_text().strip())
        except (OSError, ValueError):
            continue
        try:
            os.kill(pid, 0)                        # exists and is ours?
        except (ProcessLookupError, PermissionError):
            continue
        GUARD.watch(pid, name, expected_large=name in EXPECTED_LARGE)
        adopted.append(f"{name}:{pid}")
    return adopted
