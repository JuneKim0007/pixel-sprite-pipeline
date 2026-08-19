"""Killing a process before it takes the machine with it - watches RSS and system memory pressure from outside; a python3.12 holding 14.94 GB of 16 GB once triggered a kernel panic with no exception raised in-process."""

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

INTERVAL_S = 1.0

PRESSURE_NORMAL, PRESSURE_WARN, PRESSURE_CRITICAL = 1, 2, 4

CRITICAL_TICKS = 5


@dataclass
class Watched:
    """A process this guard may kill, and why it is allowed to."""

    pid: int
    name: str
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


    def watch(self, pid: int, name: str, *, expected_large: bool = False,
              on_kill=None) -> None:
        with self._lock:
            self.watched[pid] = Watched(pid, name, expected_large, on_kill)

    def forget(self, pid: int) -> None:
        with self._lock:
            self.watched.pop(pid, None)


    @staticmethod
    def pressure() -> int:
        """The machine's own view of how close it is. 1 normal, 4 critical."""
        try:
            out = subprocess.run(
                ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                capture_output=True, text=True, timeout=2)
            return int(out.stdout.strip() or PRESSURE_NORMAL)
        except (OSError, ValueError, subprocess.SubprocessError):
            return PRESSURE_NORMAL

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
                    found[int(parts[0])] = int(parts[1]) * 1024
                except ValueError:
                    pass
        return found


    def _kill(self, target: Watched, why: str, rss: int) -> None:
        log.error("guard: killing %s (pid %d, %.2f GB) - %s",
                  target.name, target.pid, rss / (1 << 30), why)
        self.kills.append({"pid": target.pid, "name": target.name,
                           "rss": rss, "why": why, "at": time.time()})
        try:
            os.kill(target.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
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
            target.strikes += 1
            if target.strikes >= 2:
                self._kill(target, f"resident {rss / (1<<30):.2f} GB over the "
                                   f"{ceiling / (1<<30):.2f} GB per-process limit",
                           rss)

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


    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(INTERVAL_S):
                try:
                    self.check()
                except Exception:                  # noqa: BLE001
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


EXPECTED_LARGE = {"comfy"}


def adopt_pidfiles(run_dir) -> list[str]:
    from pathlib import Path

    adopted = []
    for pidfile in sorted(Path(run_dir).glob("*.pid")):
        name = pidfile.stem
        try:
            pid = int(pidfile.read_text().strip())
        except (OSError, ValueError):
            continue
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            continue
        GUARD.watch(pid, name, expected_large=name in EXPECTED_LARGE)
        adopted.append(f"{name}:{pid}")
    return adopted
