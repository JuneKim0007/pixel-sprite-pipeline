"""The one dependency walk, over anything that declares what it needs and gives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

from .errors import Invalid


class Node(Protocol):
    """What `Stage` and `LayerSpec` genuinely share."""

    needs: frozenset[str]
    gives: frozenset[str]


def undeclared(node: str, produced: Iterable[str], allowed: Iterable[str],
               *, required: Iterable[str] = ()) -> str:
    """Why a node's answer does not match what it declared, or "" if it does."""
    extra = sorted(set(produced) - set(allowed))
    if extra:
        return (f"'{node}' returned {extra}, which it never declared. Add them "
                f"so what it produces stays checkable.")
    missing = sorted(set(required) - set(produced))
    if missing:
        return f"'{node}' declared {missing} and did not return them."
    return ""


@dataclass(frozen=True)
class Unmet:
    """One name a node asked for and did not have, and who has it."""

    node: str
    name: str
    producer: str | None      # a later node that gives it, if any


def _name_of(node: Any) -> str:
    return getattr(node, "name", None) or getattr(node, "key", "") or repr(node)


def unmet(nodes: Iterable[Any], *, seeded: frozenset[str] = frozenset(),
          supplied: Callable[[str], bool] = lambda _n: False,
          strict: bool = True) -> list[Unmet]:
    """Every dependency the order cannot satisfy, in the order they arise."""
    nodes = list(nodes)
    given = set(seeded)
    later: dict[str, str] = {}
    for node in reversed(nodes):
        for name in frozenset(getattr(node, "gives", ()) or ()):
            later[name] = _name_of(node)

    out: list[Unmet] = []
    for node in nodes:
        # A soft need in ordering mode: absent is fine, LATER is not.
        soft = frozenset(getattr(node, "optional", ()) or ())
        hard = frozenset(getattr(node, "needs", ()) or ()) - soft
        for name, strict_here in ([(n, strict) for n in sorted(hard)]
                                  + [(n, False) for n in sorted(soft)]):
            if name in given or supplied(name):
                continue
            producer = later.get(name)
            # Never given is a hole for a hard need and a non-event for a soft one.
            if producer is None and not strict_here:
                continue
            out.append(Unmet(_name_of(node), name, producer))
        given |= frozenset(getattr(node, "gives", ()) or ())
    return out


def refuse(problems: list[Unmet], *, error=Invalid,
           why: Callable[[Unmet], str] | None = None,
           hint: Callable[[Unmet], str] | None = None,
           tail: str = "") -> None:
    """Raise on the first unmet dependency, naming who would have satisfied it."""
    if not problems:
        return
    first = problems[0]
    if why is not None:
        # The caller knows the vocabulary, so the caller supplies the way forward.
        raise error(why(first), field=first.node,
                    hint=hint(first) if hint else "")
    lines = [
        f"'{p.name}' is produced by '{p.producer}', which runs later"
        if p.producer else f"'{p.name}' is produced by no configured node"
        for p in problems if p.node == first.node
    ]
    raise error(f"'{first.node}' cannot run in this order:\n  "
                + "\n  ".join(lines) + tail, field=first.node)
