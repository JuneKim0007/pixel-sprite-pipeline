"""Running a layer stack: prepare what is orderless, then walk forward."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import cache
from . import layers as layer_mod
from .layers import REGISTRY, admit, check_order


def prepare_for(spec, image: np.ndarray, cfg: dict, *, use_cache: bool = True) -> dict:
    if spec.prepare is None:
        return {}
    if not use_cache:
        return spec.prepare(image, cfg)
    key = cache.key(f"prep:{spec.key}", image, cfg)
    return cache.CACHE.get(key, lambda: spec.prepare(image, cfg))


def apply_stack(image: np.ndarray, stack: list[dict], *,
                palettes: Callable[[str], Path] | None = None,
                source: str | None = None,
                use_cache: bool = True,
                defer: set[str] | None = None) -> tuple[np.ndarray, dict]:
    defer = {k for k in (defer or set())
             if getattr(REGISTRY.get(k), "deferrable", False)}
    # A stack that cannot fit is a 413 here and a reboot two lines later.
    admit(stack, int(image.shape[0]) * int(image.shape[1]), defer)

    facts: dict[str, Any] = {
        "palettes": palettes,
        "before": {"width": int(image.shape[1]), "height": int(image.shape[0]),
                   "colours": cache.count_colours(image)},
        "layers": [],
        "warnings": check_order(stack),
        "prepared": 0,
        "reused": 0,
    }

    deferred: dict[str, Any] = {"scale": 1.0, "layers": []}

    if source and defer:
        source = f"{source}|defer={','.join(sorted(defer))}"

    start, out = 0, image
    if source:
        start, resumed = cache.resume_from(source, stack)
        if resumed is not None:
            out = resumed
    facts["resumed_after"] = start

    token = layer_mod.budget(image.shape[0] * image.shape[1])
    try:
        for index, entry in enumerate(stack):
            key = entry.get("layer")
            spec = REGISTRY.get(key)
            record: dict[str, Any] = {"id": entry.get("id") or key, "layer": key}

            if index < start:
                record["cached"] = True
                facts["layers"].append(record)
                continue
            if spec is None:
                record["error"] = f"no layer '{key}'"
                facts["layers"].append(record)
                continue
            if not entry.get("enabled", True):
                record["skipped"] = True
                facts["layers"].append(record)
                if source:
                    cache.remember(source, stack, index + 1, out)
                continue

            cfg = spec.settings(entry.get("config"))

            if spec.key in defer:
                grow = spec.growth(cfg)
                record["deferred"] = True
                deferred["scale"] *= grow ** 0.5
                deferred["layers"].append(spec.key)
                facts["layers"].append(record)
                if source:
                    cache.remember(source, stack, index + 1, out)
                continue

            try:
                before = cache.CACHE.misses
                prep = prepare_for(spec, out, cfg, use_cache=use_cache)
                record["prepared"] = cache.CACHE.misses > before
                facts["prepared" if record["prepared"] else "reused"] += 1
                out = spec.apply(out, cfg, facts, prep)
            except Exception as e:                   # noqa: BLE001
                record["error"] = f"{type(e).__name__}: {e}"
            facts["layers"].append(record)
            if source:
                cache.remember(source, stack, index + 1, out)
    finally:
        layer_mod.release(token)

    # Scale records the count before it magnifies, because magnification cannot change it and counting afterwards is 17x the work for the same number.
    facts["after"] = {"width": int(out.shape[1]), "height": int(out.shape[0]),
                      "colours": facts.pop("_colours", None)
                      if facts.get("_colours") is not None
                      else cache.count_colours(out)}

    zoom = round(deferred["scale"], 6)
    facts["deferred"] = {
        "scale": zoom,
        "layers": deferred["layers"],
        "width": int(round(out.shape[1] * zoom)),
        "height": int(round(out.shape[0] * zoom)),
    }
    facts.pop("palettes", None)
    return out, facts
