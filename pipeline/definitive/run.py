"""Running a layer stack: prepare what is orderless, then walk forward."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import cache
from . import layers as layer_mod
from .layers import REGISTRY, admit, check_order, validate_order


def prepare_for(spec, inputs: dict, cfg: dict, *, use_cache: bool = True) -> dict:
    if spec.prepare is None:
        return {}
    if not use_cache:
        return spec.prepare(inputs, cfg)
    key = cache.key(f"prep:{spec.key}", inputs["image"], cfg)
    return cache.CACHE.get(key, lambda: spec.prepare(inputs, cfg))


def _opening_facts(image: np.ndarray, stack: list[dict]) -> dict[str, Any]:
    """What the stack was handed, before any layer has run."""
    return {
        "before": {"width": int(image.shape[1]), "height": int(image.shape[0]),
                   "colours": cache.count_colours(image)},
        "layers": [],
        "warnings": check_order(stack),
        "prepared": 0,
        "reused": 0,
    }


def _closing_facts(facts: dict, out: np.ndarray, deferred: dict) -> None:
    """What the stack produced, and what a deferred layer would still do to it."""
    # Scale records the count before it magnifies: magnification cannot change the number, and counting after is 17x the work for the same answer.
    counted = facts.pop("colours", None)
    facts["after"] = {"width": int(out.shape[1]), "height": int(out.shape[0]),
                      "colours": counted if counted is not None
                      else cache.count_colours(out)}
    zoom = round(deferred["scale"], 6)
    facts["deferred"] = {
        "scale": zoom,
        "layers": deferred["layers"],
        "width": int(round(out.shape[1] * zoom)),
        "height": int(round(out.shape[0] * zoom)),
    }


class _StackRun:
    """One pass of a layer stack over one image, and what it recorded doing."""

    def __init__(self, image: np.ndarray, stack: list[dict], *,
                 palettes: Callable[[str], Path] | None,
                 source: str | None, use_cache: bool, defer: set[str]):
        self.stack = stack
        self.palettes = palettes
        self.source = source
        self.use_cache = use_cache
        self.defer = defer
        self.pixels = image.shape[0] * image.shape[1]

        self.facts = _opening_facts(image, stack)
        self.deferred: dict[str, Any] = {"scale": 1.0, "layers": []}

        self.start, self.out = 0, image
        if source:
            self.start, resumed = cache.resume_from(source, stack)
            if resumed is not None:
                self.out = resumed
        self.facts["resumed_after"] = self.start

    def _checkpoint(self, index: int) -> None:
        """Keep the image after this layer, so a later edit resumes from here."""
        if self.source:
            cache.remember(self.source, self.stack, index + 1, self.out)

    def _apply(self, spec, cfg: dict, record: dict) -> None:
        try:
            before = cache.CACHE.misses
            inputs = {"image": self.out, "palettes": self.palettes}
            prep = prepare_for(spec, inputs, cfg, use_cache=self.use_cache)
            record["prepared"] = cache.CACHE.misses > before
            self.facts["prepared" if record["prepared"] else "reused"] += 1
            produced = spec.apply(inputs, cfg, prep)
            self.out = produced.pop("image")
            self.facts.update(produced)
        except Exception as e:                   # noqa: BLE001
            record["error"] = f"{type(e).__name__}: {e}"

    def _step(self, index: int, entry: dict) -> dict:
        """What one entry did. A layer that never ran leaves no checkpoint."""
        key = entry.get("layer")
        spec = REGISTRY.get(key)
        record: dict[str, Any] = {"id": entry.get("id") or key, "layer": key}

        if index < self.start:
            record["cached"] = True
            return record
        if spec is None:
            record["error"] = f"no layer '{key}'"
            return record

        if not entry.get("enabled", True):
            record["skipped"] = True
        else:
            cfg = spec.settings(entry.get("config"))
            if spec.key in self.defer:
                record["deferred"] = True
                self.deferred["scale"] *= spec.growth(cfg) ** 0.5
                self.deferred["layers"].append(spec.key)
            else:
                self._apply(spec, cfg, record)

        self._checkpoint(index)
        return record

    def run(self) -> tuple[np.ndarray, dict]:
        token = layer_mod.budget(self.pixels)
        try:
            for index, entry in enumerate(self.stack):
                self.facts["layers"].append(self._step(index, entry))
        finally:
            layer_mod.release(token)

        _closing_facts(self.facts, self.out, self.deferred)
        return self.out, self.facts


def apply_stack(image: np.ndarray, stack: list[dict], *,
                palettes: Callable[[str], Path] | None = None,
                source: str | None = None,
                use_cache: bool = True,
                defer: set[str] | None = None) -> tuple[np.ndarray, dict]:
    defer = {k for k in (defer or set())
             if getattr(REGISTRY.get(k), "deferrable", False)}
    # An order that measures colour from pixels the next layer destroys is wrong before it is expensive, so it is refused first.
    validate_order(stack)
    # A stack that cannot fit is a 413 here and a reboot two lines later.
    admit(stack, int(image.shape[0]) * int(image.shape[1]), defer)

    if source and defer:
        source = f"{source}|defer={','.join(sorted(defer))}"

    return _StackRun(image, stack, palettes=palettes, source=source,
                     use_cache=use_cache, defer=defer).run()

