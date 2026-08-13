"""One registry, two ways of filling it.

Six existed in three implementations, and they disagreed on the things that
matter: styles raised on a duplicate name, palettes and props let the later
file win silently, and all three swallowed a malformed file. A typo made a
palette vanish, so the symptom was "the file I wrote is not in the list" with
nothing anywhere saying why.

    Decorated   entries added by a decorator at import time
    Scanned     a glob plus a parse function, cached against file mtimes

Entries need only a key. Rigs, palettes, styles and layers are four different
dataclasses, and a common base would buy nothing a key does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generic, Iterable, TypeVar

from .errors import Conflict, Invalid, NotFound

T = TypeVar("T")


@dataclass
class Broken:
    """A file that should have been an entry and was not.

    Kept rather than discarded: "your palette has a typo on line 3" is the
    message the person needs, and silence is the failure this exists to end.
    """

    path: Path
    why: str


class Source(Generic[T]):
    """Where a registry's entries come from."""

    def load(self) -> tuple[dict[str, T], list[Broken]]:
        raise NotImplementedError

    def signature(self) -> Any:
        """Cheap value that changes when the entries might have.

        `None` means "never changes", which is right for a decorated registry:
        its contents are fixed once imports finish.
        """
        return None


class Decorated(Source[T]):
    """Filled by a decorator at import time.

    A duplicate key is a programming error rather than a typo, so it is refused
    at import.
    """

    def __init__(self) -> None:
        self.entries: dict[str, T] = {}

    def add(self, key: str, value: T, *, what: str = "entry") -> T:
        if key in self.entries:
            raise Conflict(f"two {what}s both call themselves '{key}'")
        self.entries[key] = value
        return value

    def signature(self) -> Any:
        """The entry count, because a decorated registry DOES change.

        "Fixed once imports finish" is not a moment any caller can observe: a
        registry read before its modules import would otherwise cache the
        emptiness for the life of the process.
        """
        return len(self.entries)

    def load(self) -> tuple[dict[str, T], list[Broken]]:
        return dict(self.entries), []


class Scanned(Source[T]):
    """Filled from disk, and honest about what would not parse.

    `parse` returns one (key, value), a dict of many, or None to skip the file.
    Many because a palette file is one palette and a props file is a list, and
    forcing the second into the first would mean a file per sword.

    Raising rejects a file, and the message is what the user is told.
    """

    def __init__(self, base: Path, patterns: Iterable[str],
                 parse: Callable[[Path], tuple[str, T] | dict[str, T] | None],
                 *, what: str = "entry") -> None:
        self.base = Path(base)
        self.patterns = list(patterns)
        self.parse = parse
        self.what = what

    def _files(self) -> list[Path]:
        if not self.base.exists():
            return []
        out: list[Path] = []
        for pattern in self.patterns:
            out += sorted(self.base.glob(pattern))
        # A file matched by two patterns is one file.
        return sorted(set(out))

    def signature(self) -> Any:
        return tuple((str(p), p.stat().st_mtime_ns) for p in self._files())

    def load(self) -> tuple[dict[str, T], list[Broken]]:
        found: dict[str, T] = {}
        broken: list[Broken] = []
        origin: dict[str, Path] = {}

        for path in self._files():
            try:
                result = self.parse(path)
            except Exception as e:                       # noqa: BLE001
                broken.append(Broken(path, f"{type(e).__name__}: {e}"))
                continue
            if result is None:
                continue
            produced = result if isinstance(result, dict) else dict([result])
            for key, value in produced.items():
                if key in found:
                    # Two files claiming one name is a real ambiguity, and
                    # picking one silently means the other's edits appear to do
                    # nothing at all.
                    broken.append(Broken(
                        path, f"'{key}' is already defined by {origin[key]}"))
                    continue
                found[key] = value
                origin[key] = path
        return found, broken


class Registry(Generic[T]):
    """A named collection with one answer for missing, duplicate and broken."""

    def __init__(self, what: str, source: Source[T]) -> None:
        self.what = what
        self.source = source
        self._entries: dict[str, T] | None = None
        self._broken: list[Broken] = []
        self._signature: Any = object()          # never equal to a real one

    def _ensure(self) -> None:
        signature = self.source.signature()
        if self._entries is not None and signature == self._signature:
            return
        self._entries, self._broken = self.source.load()
        self._signature = signature

    def all(self) -> dict[str, T]:
        self._ensure()
        return dict(self._entries or {})

    def broken(self) -> list[Broken]:
        """Files that failed to load, so a UI can say so instead of omitting them."""
        self._ensure()
        return list(self._broken)

    def names(self) -> list[str]:
        return sorted(self.all())

    def __contains__(self, key: str) -> bool:
        return key in self.all()

    def __len__(self) -> int:
        return len(self.all())

    def get(self, key: str) -> T:
        entries = self.all()
        if key not in entries:
            # A file that failed to parse is a better answer than "not found",
            # because the person almost certainly just edited it.
            for bad in self.broken():
                if bad.path.stem == key or key in str(bad.path):
                    raise Invalid(f"{self.what} '{key}' could not be read: {bad.why}",
                                  hint=str(bad.path))
            raise NotFound(self.what, key, available=entries)
        return entries[key]

    def find(self, key: str, default: T | None = None) -> T | None:
        return self.all().get(key, default)
