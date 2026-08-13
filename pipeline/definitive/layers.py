
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Callable

from contextvars import ContextVar

from ..shared.errors import Invalid
from ..shared.registry import Decorated, Registry

# The pixel count of the image entering the stack. A layer may never be handed
# more than this: upscaling invents no detail, so analysing an enlarged image is
# analysing invented pixels, and it is what makes a reordered Scale cost 16x.
_BUDGET: ContextVar[int] = ContextVar("layer_budget", default=0)


def budget(pixels: int):
    return _BUDGET.set(int(pixels))


def release(token) -> None:
    _BUDGET.reset(token)


def _within(key: str, image) -> None:
    limit = _BUDGET.get()
    if not limit:
        return
    got = int(image.shape[0]) * int(image.shape[1])
    if got > limit:
        raise Invalid(
            f"'{key}' was handed {got / 1e6:.2f} MP from a {limit / 1e6:.2f} MP "
            f"source. Something before it enlarged the image — move Scale last.",
            field=key,
        )

_SOURCE: Decorated["LayerSpec"] = Decorated()
_LAYERS: Registry["LayerSpec"] = Registry("layer", _SOURCE)

# Kept as a name because callers read it directly; it is the same dict.
REGISTRY: dict[str, "LayerSpec"] = _SOURCE.entries


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


def _guard_prepare(key: str, fn):
    if fn is None:
        return None
    if getattr(fn, "_budgeted", False):
        return fn

    def wrapped(image, cfg):
        _within(key, image)
        return fn(image, cfg)

    wrapped._budgeted = True
    return wrapped


def _guard_apply(key: str, fn):
    if getattr(fn, "_budgeted", False):
        return fn

    def wrapped(image, cfg, facts, prep):
        _within(key, image)
        return fn(image, cfg, facts, prep)

    wrapped._budgeted = True
    return wrapped


@dataclass
class LayerSpec:

    key: str
    label: str
    summary: str
    fields: list[Field]
    apply: Callable
    prepare: Callable | None = None
    # Where this layer sits when the stack is built from scratch. Not a
    # constraint - just a sensible reading order for someone who has not
    # arranged one yet.
    order: int = 50
    repeatable: bool = False

    def __post_init__(self) -> None:
        # Guarding here rather than in the runner is the guarantee: a LayerSpec
        # cannot exist unguarded, however it is built or whoever calls it.
        self.prepare = _guard_prepare(self.key, self.prepare)
        self.apply = _guard_apply(self.key, self.apply)

    def defaults(self) -> dict:
        return {f.key: f.default for f in self.fields}

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "summary": self.summary,
            "order": self.order, "repeatable": self.repeatable,
            "fields": [f.as_dict() for f in self.fields],
        }


def layer(key: str, *, label: str, summary: str, fields: list[Field],
          order: int = 50, repeatable: bool = False,
          prepare: Callable | None = None):

    def wrap(fn):
        _SOURCE.add(key, LayerSpec(key=key, label=label, summary=summary,
                                   fields=fields, apply=fn, order=order,
                                   repeatable=repeatable, prepare=prepare),
                    what="layer")
        return fn
    return wrap


def get(key: str) -> LayerSpec:
    """One layer, with the alternatives named if it is not there."""
    return _LAYERS.get(key)


def catalogue() -> list[dict]:
    return [spec.as_dict() for spec in
            sorted(REGISTRY.values(), key=lambda s: s.order)]


def default_stack() -> list[dict]:

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
    if "scale" in seen and seen.index("scale") != len(seen) - 1:
        out.append(
            "Scale is not last. It multiplies the pixel count by the zoom "
            "squared, and every layer after it pays: measured 0.59s to 9.7s "
            "for the default zoom of 4. Layers handed more pixels than the "
            "source has are refused.")
    if seen.count("grid") > 1:
        out.append("Grid appears twice. Reducing an already-reduced image "
                   "destroys the lattice the first pass established.")
    return out
