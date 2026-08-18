
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from contextvars import ContextVar

from ..shared import limits
from ..shared.contracts import LayerField as Field
from ..shared.errors import Invalid, TooLarge
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
    """Refuse a result too big to carry, after the layer has built it.

    The backstop to admission control rather than the mechanism: by the time
    this can see the array, the array exists. It is here because admission
    control depends on every enlarging layer declaring `magnify`, and a
    guarantee that depends on an author remembering something is not one. A
    layer that grows without saying so gets caught here on its first run
    instead of on the day it meets a big enough image.
    """
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
    # How this layer multiplies the pixel count, from its settings alone. The
    # default says "produces what it was given", which is true of every layer
    # that maps colours. A layer that resizes MUST declare this or admission
    # control cannot see it coming - and the output guard in _guard_apply is
    # what catches one that forgets.
    magnify: Callable[[dict], float] | None = None
    # Whether this layer's effect can be handed to the display instead of
    # computed. True only for a transform that invents nothing and that the
    # viewer reproduces exactly: integer nearest-neighbour magnification is
    # the case, because `image-rendering: pixelated` already does it.
    deferrable: bool = False

    def __post_init__(self) -> None:
        # Guarding here rather than in the runner is the guarantee: a LayerSpec
        # cannot exist unguarded, however it is built or whoever calls it.
        self.prepare = _guard_prepare(self.key, self.prepare)
        self.apply = _guard_apply(self.key, self.apply)

    def defaults(self) -> dict:
        return {f.key: f.default for f in self.fields}

    def settings(self, cfg: dict | None) -> dict:
        """Defaults, overlaid with whatever the caller sent, clamped.

        The single place a request's numbers become a layer's settings. Every
        caller goes through it, so a field's declared bounds hold for the API
        exactly as they hold for the form.
        """
        by_key = {f.key: f for f in self.fields}
        out = self.defaults()
        for key, value in (cfg or {}).items():
            field = by_key.get(key)
            # An unknown key is passed through rather than dropped: layers read
            # cfg with .get and a stale one is harmless, while silently eating
            # it would hide a rename behind a working preview.
            out[key] = field.clamp(value) if field else value
        return out

    def growth(self, cfg: dict) -> float:
        """What this layer multiplies the pixel count by, before it runs."""
        if self.magnify is None:
            return 1.0
        try:
            return max(0.0, float(self.magnify(self.settings(cfg))))
        except Exception:                        # noqa: BLE001
            # A layer that cannot say is assumed to grow, because the safe
            # reading of "I do not know" is not "it is fine".
            return float("inf")

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "summary": self.summary,
            "order": self.order, "repeatable": self.repeatable,
            "fields": [f.as_dict() for f in self.fields],
        }


def layer(key: str, *, label: str, summary: str, fields: list[Field],
          order: int = 50, repeatable: bool = False,
          prepare: Callable | None = None,
          magnify: Callable[[dict], float] | None = None,
          deferrable: bool = False):

    def wrap(fn):
        _SOURCE.add(key, LayerSpec(key=key, label=label, summary=summary,
                                   fields=fields, apply=fn, order=order,
                                   repeatable=repeatable, prepare=prepare,
                                   magnify=magnify, deferrable=deferrable),
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


# -------------------------------------------------------- admission control


def projected_pixels(stack: list[dict], pixels: int,
                     defer: set[str] | None = None) -> int:
    """Pixels the largest intermediate will hold, worked out before running.

    Arithmetic on the settings, touching no image. That is the whole point:
    the answer costs nothing and arrives while refusing is still free, whereas
    the same fact discovered by allocating is a machine that has to be
    rebooted. Growth compounds along the stack, so a two-layer stack that each
    doubles an edge is checked as the sixteen-fold it actually is.

    The peak rather than the final size, because a stack that enlarges and then
    reduces still had to hold the enlarged image on the way through.
    """
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
