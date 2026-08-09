"""Deliberate idle between GPU tasks, so a long queue does not cook the machine.

A twenty-hour overnight batch is not twenty hours of the same load as one run.
The difference is thermal: a laptop generating continuously holds its SoC near
the throttle point for the whole night, and sustained heat is what shortens a
battery's life rather than any single hot moment. The machine will not fail —
it will throttle, finish anyway, and quietly age.

So the pipeline rests between GPU tasks. Not because anything needs the pause
to work, which is why this is easy to leave out and easy to regret.

Three properties are deliberate:

  it rests AFTER work, never before   A run that pauses before its first image
                                      feels broken. A run that pauses after
                                      one feels like it is pacing itself.

  it rests between GPU tasks only     CPU stages barely warm anything, and
                                      resting after them spends wall-clock for
                                      no thermal benefit.

  it is skipped after the last task   Nothing follows, so the rest protects
                                      nothing. Sleeping at the end of a queued
                                      job just holds the queue.

The cost is honest and should be stated wherever this is configured: at three
minutes a rest, a fifty-image night spends two and a half hours resting. That
is the trade being made, and it is only worth making because the alternative is
paid in hardware rather than in time.
"""

from __future__ import annotations

import time
from typing import Callable

DEFAULT_SECONDS = 180


def seconds(config: dict | None) -> float:
    """How long to rest, from the config's `cooling:` block."""
    cfg = (config or {}).get("cooling") or {}
    if cfg.get("enabled") is False:
        return 0.0
    value = cfg.get("seconds", DEFAULT_SECONDS)
    if value in (None, ""):
        value = DEFAULT_SECONDS
    return max(0.0, float(value))


def estimate(config: dict | None, gpu_tasks: int) -> float:
    """Total seconds a run of this shape will spend resting.

    The UI shows this before anything starts, because a number that appears
    only in the log is one people discover by watching a run seem to hang.
    """
    if gpu_tasks <= 1:
        return 0.0
    return seconds(config) * (gpu_tasks - 1)


def rest(config: dict | None, *, after: str, last: bool = False,
         report: Callable[[str], None] = print,
         sleep: Callable[[float], None] = time.sleep) -> float:
    """Pause after a GPU task. Returns the seconds actually spent.

    `sleep` is injectable so the tests can assert the policy without waiting
    three minutes to find out that it works.
    """
    if last:
        return 0.0
    wait = seconds(config)
    if wait <= 0:
        return 0.0
    report(f"   cooling {wait / 60:.0f}m after {after}")
    sleep(wait)
    return wait
