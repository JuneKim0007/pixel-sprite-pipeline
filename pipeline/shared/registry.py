"""One registry, two ways of filling it.

Six registries existed, in three implementations. Three scanned the filesystem
(palettes, styles, props) and three were filled at import time (stages, rigs,
layers), and each decided independently what to do about caching, duplicate
names, and a file that will not parse. The answers disagreed:

    styles      raises on a duplicate name
    palettes    lets the later file win, silently
    props       lets the later entry win, silently

    all three   swallow a malformed file, so a typo in a palette makes it
                vanish rather than complain
    all three   rescan and reparse on every call

A vanishing palette is the one that matters. Nothing reports it, so the failure
presents as "the palette I wrote is not in the list" and the only way to find
the cause is to notice the file is fine and go reading the scanner.

So the decisions are made once, here, and the two ways of filling a registry
are a strategy rather than two codebases:

    Decorated   a decorator adds entries as modules import
    Scanned     a glob plus a parse function, cached against file mtimes

What the entries are is deliberately unconstrained beyond needing a key. Rigs,
palettes, styles and editor layers are four different dataclasses; requiring a
common base class would buy nothing that a key does not already buy, and would
mean editing four dataclasses to gain it.
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

    Kept rather than discarded, because "your palette has a typo on line 3" is
    the message the person actually needs, and the alternative - the file
    silently not being there - is the failure mode this class exists to end.
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

    A duplicate key here is a programming error rather than a user's typo -
    two stages claiming the same name - so it is refused at import, which is
    the earliest possible moment and long before anything runs.
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

        The first version returned None here, on the reasoning that decorated
        entries are fixed once imports finish. They are - but "once imports
        finish" is not a moment any caller can observe. Something asked for the
        stage list before pipeline.stages had been imported, the registry
        cached an empty dict, and `signature() == self._signature` kept it
        empty for the life of the process. The schema then served a select with
        no options.
        """
        return len(self.entries)

    def load(self) -> tuple[dict[str, T], list[Broken]]:
        return dict(self.entries), []


class Scanned(Source[T]):
    """Filled from disk, and honest about what would not parse.

    `parse` returns one (key, value), a dict of many, or None to skip the file
    entirely. Many is not an afterthought: a palette file is one palette, but a
    props file is a list of props, and forcing the second shape into the first
    would mean a file per sword.

    Raising is the supported way to reject a file. The exception message
    becomes what the user is told, so `raise Invalid("no colours found")` reads
    better in the UI than returning None ever could.
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
    """A named collection with one answer for missing, duplicate and broken.

    `get` raises NotFound carrying the alternatives, which is the difference
    between "no palette 'pico'" and a message that also says pico8 exists.
    """

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
