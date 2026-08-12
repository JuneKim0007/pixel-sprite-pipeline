"""Filesystem operations the UI needs: browsing, uploading, downloading.

Every path from the browser is untrusted, so all of it funnels through
`safe_path`. The allowed roots are explicit — the project directory plus
whatever the user configured as input/output/download directories, which may
legitimately sit outside the project.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from .shared.errors import Denied

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class PathDenied(Denied):
    pass


def safe_path(raw: str, allowed: list[Path]) -> Path:
    """Resolve `raw` and require it to sit under one of `allowed`.

    Resolution happens before the check so that `..` segments and symlinks are
    collapsed first — testing the string would let `inputs/../../etc` through.
    """
    if not raw:
        raise PathDenied("empty path")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = allowed[0] / candidate
    candidate = candidate.resolve()

    for root in allowed:
        try:
            candidate.relative_to(root.resolve())
            return candidate
        except ValueError:
            continue
    raise PathDenied(f"path is outside the allowed directories: {raw}")


def safe_filename(name: str) -> str:
    """Reduce an uploaded filename to something harmless."""
    cleaned = SAFE_NAME.sub("_", Path(name).name).strip("._") or "upload"
    return cleaned[:120]


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    is_image: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name, "path": self.path, "is_dir": self.is_dir,
            "size": self.size, "is_image": self.is_image,
        }


def browse(directory: Path, images_only: bool = False) -> dict:
    """List a directory: sub-directories first, then files."""
    if not directory.is_dir():
        raise FileNotFoundError(f"not a directory: {directory}")

    dirs, files = [], []
    for child in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            dirs.append(Entry(child.name, str(child), True))
            continue
        is_image = child.suffix.lower() in IMAGE_SUFFIXES
        if images_only and not is_image:
            continue
        try:
            size = child.stat().st_size
        except OSError:
            size = 0
        files.append(Entry(child.name, str(child), False, size, is_image))

    parent = str(directory.parent) if directory.parent != directory else None
    return {
        "dir": str(directory),
        "parent": parent,
        "entries": [e.as_dict() for e in dirs + files],
    }


def unique_name(directory: Path, filename: str) -> Path:
    """A non-clobbering destination: name.png, name-2.png, name-3.png…"""
    dst = directory / filename
    if not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    for n in range(2, 1000):
        candidate = directory / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"cannot find a free name for {filename} in {directory}")


def parse_multipart(body: bytes, content_type: str) -> list[tuple[str, str, bytes]]:
    """Minimal multipart/form-data parser: [(field, filename, data), ...].

    The stdlib `cgi` module did this, but it is deprecated and removed in
    Python 3.13, and `email.parser` mangles binary payloads unless carefully
    coaxed. Uploads here are a handful of small images, so splitting on the
    boundary directly is simpler than either and has no deprecation horizon.
    """
    marker = "boundary="
    if marker not in content_type:
        raise ValueError("multipart request has no boundary")
    boundary = content_type.split(marker, 1)[1].strip().strip('"')
    sep = b"--" + boundary.encode()

    parts: list[tuple[str, str, bytes]] = []
    for chunk in body.split(sep):
        if chunk in (b"", b"--", b"--\r\n") or b"\r\n\r\n" not in chunk:
            continue
        raw_headers, _, data = chunk.partition(b"\r\n\r\n")
        headers = raw_headers.decode("utf-8", "replace")
        disposition = next(
            (l for l in headers.splitlines() if l.lower().startswith("content-disposition")),
            "",
        )
        if not disposition:
            continue

        def field(key: str) -> str:
            m = re.search(rf'{key}="([^"]*)"', disposition)
            return m.group(1) if m else ""

        # Trailing CRLF belongs to the boundary, not the payload.
        if data.endswith(b"\r\n"):
            data = data[:-2]
        parts.append((field("name"), field("filename"), data))
    return parts


def plan_copy(sources: list[Path], target: Path) -> dict:
    """What a download would do, without doing it.

    The UI asks for this first so it can warn about overwrites *before* the
    user commits, rather than reporting damage afterwards.
    """
    conflicts, fresh = [], []
    for src in sources:
        dst = target / src.name
        (conflicts if dst.exists() else fresh).append(
            {"name": src.name, "from": str(src), "to": str(dst)}
        )
    return {
        "target": str(target),
        "total": len(sources),
        "conflicts": conflicts,
        "new": fresh,
    }


def copy_files(sources: list[Path], target: Path, overwrite: bool) -> dict:
    """Copy into `target`. Without `overwrite`, existing names are given a suffix."""
    target.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []
    for src in sources:
        dst = target / src.name
        if dst.exists() and not overwrite:
            dst = unique_name(target, src.name)
            skipped.append(src.name)
        shutil.copy2(src, dst)
        written.append(str(dst))
    return {"written": written, "renamed": skipped, "target": str(target)}
