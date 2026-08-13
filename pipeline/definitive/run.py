"""Running a layer stack: prepare what is orderless, then walk forward.

    prepare   Everything that is a function of the image reaching a layer and
              that layer's own settings, and of nothing else. Block size,
              lattice phase, a clustered palette. Pure, so it is cached by
              content: an edit that does not reach a layer never recomputes it.

    apply     Walks forward, taking what prepare worked out and doing the cheap
              ordered thing. Nothing here measures, searches or clusters.

Preparation is interleaved rather than done up front - what Grid prepares
depends on the image Curves produced. The separation still pays, because the
cache key is the arriving image plus that layer's settings, so a preparation is
skipped whenever those match no matter what else in the stack moved.

A layer that raises does not kill the run: a half-typed hex colour should show
an error against that layer rather than blanking the preview. The image passes
through unchanged and the failure lands in `facts`.

Alpha travels with the image, so layers receive whatever the previous one
returned rather than a normalised RGB array.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import cache
from . import layers as layer_mod
from .layers import REGISTRY, check_order


def prepare_for(spec, image: np.ndarray, cfg: dict, *, use_cache: bool = True) -> dict:
    """The orderless half of one layer, from cache when the inputs match.

    Keyed on the arriving image and this layer's settings and nothing else,
    which is the claim that makes it orderless.
    """
    if spec.prepare is None:
        return {}
    if not use_cache:
        return spec.prepare(image, cfg)
    key = cache.key(f"prep:{spec.key}", image, cfg)
    return cache.CACHE.get(key, lambda: spec.prepare(image, cfg))


def apply_stack(image: np.ndarray, stack: list[dict], *,
                root: Path | None = None,
                source: str | None = None,
                use_cache: bool = True) -> tuple[np.ndarray, dict]:
    """Prepare and apply every enabled layer in order.

    `source` opts into resuming from a snapshot partway through: moving one
    slider changes one layer, and every layer before it is unchanged by
    definition. A pipeline stage passes none - it runs once, on an image
    nothing will ask about again.
    """
    facts: dict[str, Any] = {
        "root": root,
        "before": {"width": int(image.shape[1]), "height": int(image.shape[0]),
                   "colours": cache.count_colours(image)},
        "layers": [],
        "warnings": check_order(stack),
        "prepared": 0,
        "reused": 0,
    }

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
                record["cached"] = True             # in the snapshot resumed from
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

            cfg = {**spec.defaults(), **(entry.get("config") or {})}
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

    # Scale records the count before it magnifies, because magnification
    # cannot change it and counting afterwards is 17x the work for the same
    # number.
    facts["after"] = {"width": int(out.shape[1]), "height": int(out.shape[0]),
                      "colours": facts.pop("_colours", None)
                      if facts.get("_colours") is not None
                      else cache.count_colours(out)}
    facts.pop("root", None)
    return out, facts
