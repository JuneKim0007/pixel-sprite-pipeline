"""One registry, two ways of filling it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generic, Iterable, TypeVar

from .errors import Conflict, Invalid, NotFound

T = TypeVar("T")


@dataclass
class Broken:
    """A file that should have been an entry and was not."""

    path: Path
    why: str


class Source(Generic[T]):
    """Where a registry's entries come from."""

    def load(self) -> tuple[dict[str, T], list[Broken]]:
        raise NotImplementedError  # not-a-message: reaching it means one was built without doing that

    def signature(self) -> Any:
        return None


class Decorated(Source[T]):
    """Filled by a decorator at import time."""

    def __init__(self) -> None:
        self.entries: dict[str, T] = {}

    def add(self, key: str, value: T, *, what: str = "entry") -> T:
        if key in self.entries:
            raise Conflict(f"two {what}s both call themselves '{key}'")
        self.entries[key] = value
        return value

    def signature(self) -> Any:
        return len(self.entries)

    def load(self) -> tuple[dict[str, T], list[Broken]]:
        return dict(self.entries), []


class Scanned(Source[T]):
    """Filled from disk, and honest about what would not parse."""

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
                    # Two files claiming one name is a real ambiguity, and picking one silently means the other's edits appear to do nothing at all.
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
        self._signature: Any = object()

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
            for bad in self.broken():
                if bad.path.stem == key or key in str(bad.path):
                    raise Invalid(f"{self.what} '{key}' could not be read: {bad.why}",
                                  hint=str(bad.path))
            raise NotFound(self.what, key, available=entries)
        return entries[key]

    def find(self, key: str, default: T | None = None) -> T | None:
        return self.all().get(key, default)
