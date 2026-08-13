
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

FILENAME = "history.jsonl"
KINDS = ("context", "tune", "train", "note")

# A line longer than this is a bug or an attempt to store an image inline.
MAX_LINE = 256 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(path: Path, *, chunk: int = 1 << 20) -> str:
    """Content hash, streamed — a training set can be hundreds of megabytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def fingerprint(path: Path) -> dict[str, Any]:
    """What we keep about a file we are not keeping."""
    stat = path.stat()
    return {
        "name": path.name,
        "bytes": stat.st_size,
        "sha256": digest(path),
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
            timespec="seconds"),
    }


@dataclass
class Event:
    kind: str
    at: str = field(default_factory=_now)
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"at": self.at, "kind": self.kind,
                "summary": self.summary, **self.detail}


def path_for(home: Path) -> Path:
    return home / FILENAME


def append(home: Path, event: Event) -> Path:
    """Add one event. Never rewrites, so a corrupt line cannot lose the rest.

    Opened in append mode with a single write call: on a local filesystem a
    write below the pipe buffer is atomic enough that a crashed autopilot
    leaves a truncated last line rather than an interleaved one, and the
    reader below tolerates exactly that.
    """
    if event.kind not in KINDS:
        raise ValueError(f"unknown history event '{event.kind}'; expected one of {KINDS}")

    home.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event.as_dict(), ensure_ascii=False) + "\n"
    if len(line) > MAX_LINE:
        raise ValueError(
            f"history event is {len(line)} bytes. The log records what happened, "
            f"not the data it happened to — store a manifest, not the payload."
        )

    target = path_for(home)
    with target.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return target


def read(home: Path) -> list[dict[str, Any]]:
    """Newest first. A malformed line is reported, not fatal.

    A history that refuses to load because one line is broken is worse than
    one with a gap in it, and the gap is visible either way.
    """
    target = path_for(home)
    if not target.exists():
        return []

    out: list[dict[str, Any]] = []
    for number, raw in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as exc:
            out.append({"at": "", "kind": "note", "corrupt": True,
                        "summary": f"unreadable history line {number}: {exc}"})
            continue
        if isinstance(entry, dict):
            out.append(entry)
    out.sort(key=lambda e: e.get("at", ""), reverse=True)
    return out


def stream(home: Path) -> Iterator[dict[str, Any]]:
    yield from read(home)



def context_event(added: list[Path], removed: list[Path] | None = None,
                  *, home: Path, note: str = "") -> Event:
    removed = removed or []
    parts = []
    if added:
        parts.append(f"+{len(added)} exemplar" + ("s" if len(added) != 1 else ""))
    if removed:
        parts.append(f"-{len(removed)}")
    return Event(
        kind="context",
        summary=note or ", ".join(parts) or "context unchanged",
        detail={
            "added": [_relative(p, home) for p in added],
            "removed": [_relative(p, home) for p in removed],
            "files": [fingerprint(p) for p in added if p.exists()],
        },
    )


def tune_event(axis: str, before: Any, after: Any, *,
               subjects: list[str] | None = None,
               seeds: list[int] | None = None,
               trials: int = 0, note: str = "") -> Event:
    """A setting moved, and the evidence for it.

    Seeds are recorded because a comparison across different seeds measures
    seed luck rather than the setting, so a tune event without them is not
    evidence and should be readable as such.
    """
    return Event(
        kind="tune",
        summary=note or f"{axis}: {before} → {after}",
        detail={"axis": axis, "before": before, "after": after,
                "subjects": subjects or [], "seeds": seeds or [],
                "trials": trials},
    )


def train_event(*, lora: Path | None, images: list[Path],
                settings: dict[str, Any] | None = None,
                archived_to: str = "", note: str = "",
                thumbnails: list[str] | None = None) -> Event:
    """A LoRA was produced. The dataset is fingerprinted, not stored."""
    files = [fingerprint(p) for p in images if p.exists()]
    total = sum(f["bytes"] for f in files)
    return Event(
        kind="train",
        summary=note or (
            f"trained {lora.name if lora else 'a LoRA'} on {len(files)} images"),
        detail={
            "lora": ({"name": lora.name, "bytes": lora.stat().st_size,
                      "sha256": digest(lora)} if lora and lora.exists() else None),
            "dataset": {
                "count": len(files),
                "bytes": total,
                "files": files,
                # One hash over the sorted per-file hashes: identifies the set
                # as a whole, so two trainings on the same images are visibly
                # the same training even if the folder was rebuilt.
                "sha256": hashlib.sha256(
                    "".join(sorted(f["sha256"] for f in files)).encode()
                ).hexdigest(),
            },
            "settings": settings or {},
            "archived_to": archived_to,
            "thumbnails": thumbnails or [],
        },
    )


def note_event(text: str) -> Event:
    return Event(kind="note", summary=text.strip()[:400])


def _relative(path: Path, home: Path) -> str:
    try:
        return str(path.resolve().relative_to(home.resolve()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- archiving


def archive_training(home: Path, *, keep_thumbnails: int = 8) -> dict[str, Any]:
    """Move the training images aside once they have been used.

    Returns the manifest the caller should put in the train event. The images
    go to `training/archive/<date>/` rather than being deleted, because "I can
    always regenerate them" is true right up until the run that made them has
    been pruned.
    """
    source = home / "training" / "images"
    if not source.is_dir():
        return {"moved": 0, "to": ""}

    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = home / "training" / "archive" / stamp
    target.mkdir(parents=True, exist_ok=True)

    moved = 0
    for image in sorted(source.iterdir()):
        if image.is_file():
            image.rename(target / image.name)
            moved += 1
    return {"moved": moved, "to": str(target.relative_to(home))}
