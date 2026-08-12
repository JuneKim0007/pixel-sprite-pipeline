"""The definitive layer: an ordered, editable stack rather than a fixed chain.

The pixelisation used to be eight calls in a fixed order inside one server
function. That order was mostly right, and being right is not the same as being
expressible: "choose a palette, adjust it, then set the block size" is a
reasonable thing to want and there was no way to say it. Worse, where a step
sits changes what it does. Curves before quantisation decide which palette
entries get picked; curves after it just move colours off the palette again.
A pipeline whose order matters and cannot be reordered is a pipeline with one
opinion baked in.

So layers are objects in a list. The list is the order.

Each layer declares its fields here, in one place, and three things read that
declaration: the form the browser renders, the values the server validates, and
the settings a style sheet can pin. Declaring once is not tidiness — it is what
makes "every configurable value has a (?) beside it" structural. A field that
exists has a label and an explanation because there is nowhere else to define
one.

Ordering is free except where physics forbids it, and the two constraints are
named rather than enforced by hiding the control:

    palette after grid    a palette measured from full-resolution pixels does
                          not describe the reduced image it will be applied to
    key after grid        block reduction mixes the backdrop into edge pixels,
                          and a keyer that runs first has nothing to remove

Both are reported as warnings, not blocks - the same treatment the stage runner
gives a questionable pipeline order. Someone deliberately keying first to see
what happens is doing something legitimate.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Callable

REGISTRY: dict[str, "LayerSpec"] = {}


@dataclass
class Field:
    """One configurable value, and everything any consumer needs to know."""

    key: str
    label: str
    kind: str = "float"                       # float | int | bool | select | text
    help: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[tuple[str, str]] = dc_field(default_factory=list)
    default: Any = None
    # A control that only makes sense when another is set a certain way.
    when: dict[str, Any] = dc_field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "kind": self.kind,
            "help": self.help, "min": self.min, "max": self.max,
            "step": self.step, "options": [list(o) for o in self.options],
            "default": self.default, "when": self.when,
        }


@dataclass
class LayerSpec:
    key: str
    label: str
    summary: str
    fields: list[Field]
    apply: Callable
    # Where this layer sits when the stack is built from scratch. Not a
    # constraint - just a sensible reading order for someone who has not
    # arranged one yet.
    order: int = 50
    repeatable: bool = False

    def defaults(self) -> dict:
        return {f.key: f.default for f in self.fields}

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "summary": self.summary,
            "order": self.order, "repeatable": self.repeatable,
            "fields": [f.as_dict() for f in self.fields],
        }


def layer(key: str, *, label: str, summary: str, fields: list[Field],
          order: int = 50, repeatable: bool = False):
    """Register a layer. The decorated function is its apply()."""
    def wrap(fn):
        REGISTRY[key] = LayerSpec(key=key, label=label, summary=summary,
                                  fields=fields, apply=fn, order=order,
                                  repeatable=repeatable)
        return fn
    return wrap


def catalogue() -> list[dict]:
    return [spec.as_dict() for spec in
            sorted(REGISTRY.values(), key=lambda s: s.order)]


def default_stack() -> list[dict]:
    """The stack someone gets before they arrange one.

    It is the order the fixed chain used, because that order was measured and
    is still the right default. What changed is that it is now a starting point
    rather than the only possibility.
    """
    return [{"layer": s.key, "id": f"{s.key}0", "enabled": True,
             "config": s.defaults()}
            for s in sorted(REGISTRY.values(), key=lambda s: s.order)]


# ----------------------------------------------------------------- ordering


def check_order(stack: list[dict]) -> list[str]:
    """Problems with an arrangement, in words. Empty means nothing to say."""
    seen: list[str] = [s.get("layer") for s in stack if s.get("enabled", True)]
    out: list[str] = []

    def before(a: str, b: str) -> bool:
        return a in seen and b in seen and seen.index(a) < seen.index(b)

    if before("palette", "grid"):
        out.append(
            "Palette runs before Grid. A palette measured from full-resolution "
            "pixels does not describe the reduced image it gets applied to, so "
            "the colours will not be the ones the picture is made of.")
    if before("background", "grid"):
        out.append(
            "Background runs before Grid. Block reduction mixes the backdrop "
            "into every edge pixel, so keying first leaves a fringe that the "
            "reduction then spreads.")
    if seen.count("grid") > 1:
        out.append("Grid appears twice. Reducing an already-reduced image "
                   "destroys the lattice the first pass established.")
    return out
