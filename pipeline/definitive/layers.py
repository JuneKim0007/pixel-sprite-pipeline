
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from contextvars import ContextVar

from ..shared import limits
from ..shared.contracts import LayerField as Field
from ..shared import plan
from ..shared.errors import Invalid, TooLarge
from ..shared.registry import Decorated, Registry

# [...] enlarged image is analysing invented pixels, and it is what makes a reordered Scale cost 16x
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

REGISTRY: dict[str, "LayerSpec"] = _SOURCE.entries


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


def _produced(key: str, image) -> None:
    ceiling = limits.get("output_pixels")
    got = int(image.shape[0]) * int(image.shape[1])
    if got > ceiling:
        raise TooLarge(
            f"'{key}' produced {got / 1e6:.1f} MP, over the {ceiling / 1e6:.1f} MP "
            f"a single result may hold.",
            field=key,
            hint="If this layer resizes, it needs to declare magnify= so the "
                 "size is checked before the memory is spent rather than after.",
        )


def _guard_apply(key: str, fn):
    if getattr(fn, "_budgeted", False):
        return fn

    def wrapped(image, cfg, facts, prep):
        _within(key, image)
        out = fn(image, cfg, facts, prep)
        if getattr(out, "shape", None) is not None and len(out.shape) >= 2:
            _produced(key, out)
        return out

    wrapped._budgeted = True
    return wrapped


def _field_as_dict(f: Field) -> dict:
    d = f.declared()
    d["options"] = [list(o) for o in d["options"]]
    return d


@dataclass
class LayerSpec:

    key: str
    label: str
    summary: str
    fields: list[Field]
    apply: Callable
    prepare: Callable | None = None
    order: int = 50
    repeatable: bool = False
    magnify: Callable[[dict], float] | None = None
    deferrable: bool = False
    # What this layer needs to already be true of the image, and what it makes true. A need nothing gives is satisfied vacuously - no grid means no lattice to contradict - but a need given LATER is an order that produces a silently wrong picture.
    needs: frozenset[str] = frozenset()
    gives: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        self.prepare = _guard_prepare(self.key, self.prepare)
        self.apply = _guard_apply(self.key, self.apply)

    def defaults(self) -> dict:
        return {f.key: f.default for f in self.fields}

    def settings(self, cfg: dict | None) -> dict:
        by_key = {f.key: f for f in self.fields}
        out = self.defaults()
        for key, value in (cfg or {}).items():
            field = by_key.get(key)
            # An unknown key is passed through rather than dropped: layers read cfg with .get and a stale one is harmless, while silently eating it would hide a rename behind a working preview.
            out[key] = field.clamp(value) if field else value
        return out

    def growth(self, cfg: dict) -> float:
        """What this layer multiplies the pixel count by, before it runs."""
        if self.magnify is None:
            return 1.0
        try:
            return max(0.0, float(self.magnify(self.settings(cfg))))
        except Exception:                        # noqa: BLE001
            return float("inf")

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "summary": self.summary,
            "order": self.order, "repeatable": self.repeatable,
            "fields": [_field_as_dict(f) for f in self.fields],
        }


def layer(key: str, *, label: str, summary: str, fields: list[Field],
          order: int = 50, repeatable: bool = False,
          prepare: Callable | None = None,
          magnify: Callable[[dict], float] | None = None,
          deferrable: bool = False,
          needs: frozenset[str] = frozenset(),
          gives: frozenset[str] = frozenset()):

    def wrap(fn):
        _SOURCE.add(key, LayerSpec(key=key, label=label, summary=summary,
                                   fields=fields, apply=fn, order=order,
                                   repeatable=repeatable, prepare=prepare,
                                   magnify=magnify, deferrable=deferrable,
                                   needs=needs, gives=gives),
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


def projected_pixels(stack: list[dict], pixels: int,
                     defer: set[str] | None = None) -> int:
    defer = defer or set()
    peak = current = float(pixels)
    for entry in stack:
        if not entry.get("enabled", True):
            continue
        spec = REGISTRY.get(entry.get("layer"))
        if spec is None or spec.key in defer:
            continue
        current *= spec.growth(entry.get("config") or {})
        peak = max(peak, current)
        if peak == float("inf"):
            break
    return int(min(peak, float(1 << 62)))


def admit(stack: list[dict], pixels: int, defer: set[str] | None = None) -> None:
    """Refuse a stack that cannot fit, naming the layer that made it too big."""
    ceiling = limits.get("output_pixels")
    want = projected_pixels(stack, pixels, defer)
    if want <= ceiling:
        return
    culprit = max(
        (e for e in stack
         if e.get("enabled", True) and REGISTRY.get(e.get("layer"))),
        key=lambda e: REGISTRY[e["layer"]].growth(e.get("config") or {}),
        default=None)
    name = culprit.get("layer") if culprit else ""
    raise TooLarge(
        f"This stack would compute {want / 1e6:.1f} MP from a "
        f"{pixels / 1e6:.1f} MP image, over the {ceiling / 1e6:.1f} MP limit.",
        field=name,
        hint=(f"'{name}' is what multiplies it. Lower it, or turn it off and "
              f"let the viewer magnify instead." if name else
              "Use a smaller source image."),
        projected=want, ceiling=ceiling)


# Why a particular layer needs what it needs. The dependency is declared on the layer; this is the sentence a person gets when they break it.
WHY: dict[str, str] = {
    "palette": "A palette measured from full-resolution pixels does not "
               "describe the reduced image it gets applied to, so the colours "
               "will not be the ones the picture is made of.",
    "background": "Block reduction mixes the backdrop into every edge pixel, "
                  "so keying first leaves a fringe that the reduction then "
                  "spreads.",
}


def validate_order(stack: list[dict]) -> None:
    """Refuse an arrangement that would produce a silently wrong picture.

    The advisory half is `check_order`. This half is the same walk the pipeline
    runner makes over stages, in `shared/plan.py` — the difference is only that
    a layer need nothing gives at all is consistent, so the walk runs in its
    ordering mode rather than its strict one.
    """
    enabled = [REGISTRY.get(s.get("layer")) for s in stack
               if s.get("enabled", True)]
    problems = plan.unmet([n for n in enabled if n is not None], strict=False)
    plan.refuse(
        problems,
        why=lambda p: (
            f"{REGISTRY[p.node].label} runs before {REGISTRY[p.producer].label}. "
            + WHY.get(p.node, f"It needs '{p.name}', which "
                              f"{REGISTRY[p.producer].label} produces later.")),
        hint=lambda p: (f"move {REGISTRY[p.node].label} after "
                        f"{REGISTRY[p.producer].label}"))


def check_order(stack: list[dict]) -> list[str]:
    """Arrangements that cost something without being wrong. The ones that ARE wrong are refused by `validate_order`."""
    seen: list[str] = [s.get("layer") for s in stack if s.get("enabled", True)]
    out: list[str] = []

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
