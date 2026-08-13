

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST = "artifacts.json"


def _encode(value: Any) -> dict:
    if isinstance(value, Path):
        return {"type": "path", "value": str(value)}
    if isinstance(value, list) and value and all(isinstance(v, Path) for v in value):
        return {"type": "paths", "value": [str(v) for v in value]}
    try:
        json.dumps(value)
        return {"type": "json", "value": value}
    except TypeError as e:
        # Storing repr() here would hand a resumed run a string where a stage
        # expects an object. Scratch keys are already filtered, so this is a bug.
        raise TypeError(
            f"artifact {type(value).__name__} cannot be persisted, so the run "
            f"would not be resumable. Prefix the key with '_' if it is scratch."
        ) from e


def _decode(entry: dict) -> Any:
    kind, value = entry.get("type"), entry.get("value")
    if kind == "path":
        return Path(value)
    if kind == "paths":
        return [Path(v) for v in value]
    return value


def save(outdir: Path, artifacts: dict[str, Any], completed: list[str]) -> Path:
    path = outdir / MANIFEST

    persisted = {k: v for k, v in artifacts.items() if not k.startswith("_")}
    path.write_text(
        json.dumps(
            {
                "completed": completed,
                "artifacts": {k: _encode(v) for k, v in persisted.items()},
            },
            indent=2,
        )
    )
    return path


def load(outdir: Path) -> tuple[dict[str, Any], list[str]]:
    path = outdir / MANIFEST
    if not path.exists():
        raise FileNotFoundError(f"no {MANIFEST} in {outdir} — nothing to resume")
    data = json.loads(path.read_text())

    # Tolerate manifests written before this format existed.
    raw = data.get("artifacts", data)
    if raw and not isinstance(next(iter(raw.values()), None), dict):
        raise ValueError(
            f"{path} is in the old untyped format and cannot be resumed. "
            f"Start a fresh run."
        )

    artifacts = {k: _decode(v) for k, v in raw.items()}
    missing = [
        str(p)
        for v in artifacts.values()
        for p in (v if isinstance(v, list) else [v])
        if isinstance(p, Path) and not p.exists()
    ]
    if missing:
        raise FileNotFoundError(
            f"{len(missing)} artifact file(s) referenced by {path} are gone, "
            f"e.g. {missing[0]}"
        )
    return artifacts, data.get("completed", [])
