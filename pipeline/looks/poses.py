from __future__ import annotations

import json
from pathlib import Path

from ..shared import paths
from ..shared.errors import NotFound


def discover(root: Path) -> dict[str, Path]:
    base = paths.resolve(Path(root), "poses")
    return {str(p.relative_to(base).with_suffix("")): p
            for p in sorted(base.rglob("*.json"))}


def load(root: Path, name: str) -> dict:
    found = discover(root)
    if name not in found:
        raise NotFound("pose", name, available=found)
    return json.loads(found[name].read_text())
