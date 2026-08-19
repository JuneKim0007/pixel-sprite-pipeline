

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..shared.errors import Invalid, NotFound

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
        raise TypeError(
            # not-a-message: save() only ever calls _encode() on artifacts
            # already stripped of scratch (`_`-prefixed) keys, so an
            # unpersistable value here means a stage handed back something it
            # should not have — a defect upstream, not a message for the
            # caller who asked to save.
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
        raise NotFound("manifest", str(path), hint="nothing to resume")
    data = json.loads(path.read_text())

    raw = data.get("artifacts", data)
    if raw and not isinstance(next(iter(raw.values()), None), dict):
        raise Invalid(
            f"{path} is in the old untyped format and cannot be resumed.",
            hint="start a fresh run",
        )

    artifacts = {k: _decode(v) for k, v in raw.items()}
    missing = [
        str(p)
        for v in artifacts.values()
        for p in (v if isinstance(v, list) else [v])
        if isinstance(p, Path) and not p.exists()
    ]
    if missing:
        raise NotFound(
            "artifact file", missing[0],
            hint=f"{len(missing)} artifact file(s) referenced by {path} are gone",
        )
    return artifacts, data.get("completed", [])
