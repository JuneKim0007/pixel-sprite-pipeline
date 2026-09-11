from __future__ import annotations

from pathlib import Path

LAYOUT: dict[str, str] = {
    "configs": "library/configs",
    "experiments": "library/configs/experiments",
    "modules": "library/modules",
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


# The three directories a config may override, and the layout entry each names.
GLOBAL_KEYS: dict[str, str] = {
    "input_dir": "refs",
    "output_dir": "runs",
    "download_dir": "exports",
}


def from_config(root: Path, cfg: dict | None, key: str) -> Path:
    """A directory a config may override, by the key a config uses for it."""
    given = ((cfg or {}).get("paths") or {}).get(key)
    return resolve(root, GLOBAL_KEYS[key], {GLOBAL_KEYS[key]: given} if given else None)


def resolve(root: Path, name: str, overrides: dict | None = None) -> Path:
    raw = str((overrides or {}).get(name) or LAYOUT[name]).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path(root) / path
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
