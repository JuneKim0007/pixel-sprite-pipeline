"""What a route promises its response contains."""

from __future__ import annotations

from typing import Any

from ..shared.errors import Invalid

Kind = type | tuple[type, ...]


class Shape:
    """The keys a response must carry, and the type of each. Extra keys pass: a handler may answer with more than it promised, never with less."""

    def __init__(self, **keys: Kind) -> None:
        for name, kind in keys.items():
            flat = kind if isinstance(kind, tuple) else (kind,)
            if not flat or not all(isinstance(k, type) for k in flat):
                raise Invalid(f"'{name}' is declared as {kind!r}, which is not "
                              f"a type", field=name)
        self.keys: dict[str, Kind] = keys

    def check(self, body: Any) -> list[str]:
        """Every way this body breaks the promise, not just the first."""
        if not isinstance(body, dict):
            return [f"answered with {type(body).__name__}, not an object"]
        faults = []
        for name, kind in sorted(self.keys.items()):
            if name not in body:
                faults.append(f"'{name}' is missing")
            elif not isinstance(body[name], kind) and body[name] is not None:
                want = "/".join(k.__name__ for k in
                                (kind if isinstance(kind, tuple) else (kind,)))
                faults.append(f"'{name}' is {type(body[name]).__name__}, "
                              f"not {want}")
        return faults

    def __repr__(self) -> str:
        return "Shape(" + ", ".join(
            f"{n}={(k if isinstance(k, tuple) else (k,))[0].__name__}"
            for n, k in sorted(self.keys.items())) + ")"


class Bytes(Shape):
    """A route that answers with a file rather than an object."""

    def __init__(self) -> None:
        super().__init__()

    def check(self, body: Any) -> list[str]:
        return [] if isinstance(body, (bytes, bytearray)) else [
            f"answered with {type(body).__name__}, not bytes"]

    def __repr__(self) -> str:
        return "Bytes()"


class Anything(Shape):
    """A response whose shape is the caller's own echo, so there is nothing to promise. Every use is named at its route, so this cannot become the quiet default."""

    def check(self, body: Any) -> list[str]:
        return []

    def __repr__(self) -> str:
        return "Anything()"
