
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

    if gpu_tasks <= 1:
        return 0.0
    return seconds(config) * (gpu_tasks - 1)


def rest(config: dict | None, *, after: str, last: bool = False,
         report: Callable[[str], None] = print,
         sleep: Callable[[float], None] = time.sleep) -> float:

    if last:
        return 0.0
    wait = seconds(config)
    if wait <= 0:
        return 0.0
    report(f"   cooling {wait / 60:.0f}m after {after}")
    sleep(wait)
    return wait
