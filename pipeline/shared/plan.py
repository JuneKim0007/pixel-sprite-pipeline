"""The one dependency walk, over anything that declares what it needs and gives.

Two engines run nodes — the pipeline runner over stages, `apply_stack` over
editor layers — and they had the same walk written twice with two vocabularies.
What differs is only how a name may be satisfied, and that is a parameter:

  strict   a name must already be given, or seeded, or supplied by the run.
           A pipeline stage cannot invent an artifact it was not handed.

  ordering a name must not be given LATER. A stack with no `grid` has no
           lattice for `palette` to contradict, so a name nothing gives at all
           is consistent — the failure is doing it in the wrong order, not
           doing it at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

from .errors import Invalid


class Node(Protocol):
    """What the walk needs to know. Both `Stage` and `LayerSpec` satisfy it."""

    needs: frozenset[str]
    gives: frozenset[str]


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
        # A soft need is the same check in ordering mode: absent is fine, LATER is not. It is declared apart from `needs` because absent-is-fine is a property of the node, while strict-vs-ordering is a property of the engine. Naming one in both reads as "optional", so `optional` wins.
        soft = frozenset(getattr(node, "optional", ()) or ())
        hard = frozenset(getattr(node, "needs", ()) or ()) - soft
        for name, strict_here in ([(n, strict) for n in sorted(hard)]
                                  + [(n, False) for n in sorted(soft)]):
            if name in given or supplied(name):
                continue
            producer = later.get(name)
            # Not given anywhere is a hole when the need is hard and a non-event when it is soft, which is the whole difference between the two engines.
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
        # A refusal with no way forward is a dead end, so the caller that knows the vocabulary supplies the way forward.
        raise error(why(first), field=first.node,
                    hint=hint(first) if hint else "")
    lines = [
        f"'{p.name}' is produced by '{p.producer}', which runs later"
        if p.producer else f"'{p.name}' is produced by no configured node"
        for p in problems if p.node == first.node
    ]
    raise error(f"'{first.node}' cannot run in this order:\n  "
                + "\n  ".join(lines) + tail, field=first.node)
