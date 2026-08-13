from __future__ import annotations

from pathlib import Path

LAYOUT: dict[str, str] = {
    "configs": "library/configs",
    "experiments": "library/configs/experiments",
    "styles": "library/styles",
    "palettes": "library/palettes",
    "poses": "library/poses",
    "props": "library/props",
    "refs": "library/refs",
    "runs": "out/runs",
    "exports": "out/exports",
    "scratch": "out/scratch",
    "training": "out/training",
    "queue": "var/queue",
    "logs": "var/logs",
    "overnight": "var/overnight",
}


def resolve(root: Path, name: str, overrides: dict | None = None) -> Path:
    raw = str((overrides or {}).get(name) or LAYOUT[name]).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path(root) / path
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
