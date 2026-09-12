"""Removing what a run leaves behind, by named scope."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ..shared import paths


@dataclass(frozen=True)
class Scope:
    """One thing a person can ask to be rid of."""

    label: str
    note: str


SCOPES: dict[str, Scope] = {
    "logs": Scope("Logs", "Service logs. A run's own log lives with the run."),
    "runs": Scope("Outputs", "Every run directory: images, logs and scores."),
    "history": Scope("Style history", "What each style sheet records about itself."),
    "exports": Scope("Exports", "Sheets written out for use elsewhere."),
}


def _clear_dir(base: Path) -> int:
    removed = 0
    for entry in sorted(base.iterdir()):
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        removed += 1
    return removed


def _truncate_logs(base: Path) -> int:
    """Truncate rather than unlink: a log being written is held open, and
    removing it leaves the writer's descriptor on an inode nothing can reach."""
    cleared = 0
    for entry in sorted(base.glob("*.log")):
        with entry.open("w"):
            pass
        cleared += 1
    for entry in sorted(base.iterdir()):
        if entry.suffix != ".log":
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            cleared += 1
    return cleared


def _clear_history(root: Path) -> int:
    from ..looks import stylelog

    cleared = 0
    for found in sorted(paths.resolve(root, "styles").rglob(stylelog.FILENAME)):
        found.unlink(missing_ok=True)
        cleared += 1
    return cleared


def counts(root: Path) -> dict[str, int]:
    """What each scope would remove, so a person can be told before they agree."""
    out = {"logs": len(list(paths.resolve(root, "logs").iterdir())),
           "runs": len(list(paths.resolve(root, "runs").iterdir())),
           "exports": len(list(paths.resolve(root, "exports").iterdir()))}
    from ..looks import stylelog

    out["history"] = len(list(paths.resolve(root, "styles").rglob(stylelog.FILENAME)))
    return out


def wipe(root: Path, scopes: list[str]) -> dict[str, int]:
    """Remove the named scopes. Unknown names are refused rather than ignored."""
    from ..shared.errors import Invalid

    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise Invalid(f"no such thing to clear: {', '.join(unknown)}",
                      field="scopes", hint=f"one of {', '.join(SCOPES)}")

    removed: dict[str, int] = {}
    for scope in scopes:
        if scope == "logs":
            removed[scope] = _truncate_logs(paths.resolve(root, "logs"))
        elif scope == "history":
            removed[scope] = _clear_history(root)
        else:
            removed[scope] = _clear_dir(paths.resolve(root, scope))
    return removed
