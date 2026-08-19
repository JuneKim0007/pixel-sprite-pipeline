"""Reading configuration, without knowing what any of it means."""

from __future__ import annotations

from typing import Any


def opt(cfg: dict, key: str, default: Any) -> Any:
    value = cfg.get(key, None)
    return default if value is None else value
