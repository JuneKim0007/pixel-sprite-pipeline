"""Executing a layer stack.

The whole of it is a loop, which is the point: the interesting decisions moved
into the stack itself, so running one has nothing left to decide.

Three behaviours are worth stating because they are not obvious from the loop.

A run can start partway. Moving one slider changes one layer, so the image
arriving at that layer is the same as it was a moment ago - and if it was kept,
the layers before it need not run at all. That is what `source` opts into.

A layer that raises does not kill the run. The editor is interactive and a
half-typed hex colour or a palette file that has been moved should show an
error against that layer rather than blanking the preview - the same reason the
queue distinguishes a broken job from a stalled one. The image passes through
unchanged and the failure is reported in `facts`.

Alpha travels with the image. `background` produces a fourth channel and
everything after it has to preserve it, so layers receive whatever the previous
one returned rather than a normalised RGB array. Anything that only understands
three channels slices them itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import cache
from .layers import REGISTRY, check_order


def apply_stack(image: np.ndarray, stack: list[dict], *,
                root: Path | None = None,
                source: str | None = None) -> tuple[np.ndarray, dict]:
    """Run every enabled layer in order, resuming from the longest known prefix.

    `source` opts into that resumption. Moving one slider changes exactly one
    layer, and every layer before it is unchanged by definition - so with a
    snapshot of the image at that point there is nothing to recompute. A change
    at the end of a five-layer stack costs one layer; a change at the start
    costs five, which is the honest shape of the work.

    Passing no `source` runs the whole stack, which is what a pipeline stage
    wants: it runs once, on an image nothing else will ask about again, and
    keeping snapshots for it would evict the editor's for nothing.
    """
    facts: dict[str, Any] = {
        "root": root,
        "before": {"width": int(image.shape[1]), "height": int(image.shape[0]),
                   "colours": int(len(np.unique(image.reshape(-1, image.shape[2])[:, :3],
                                                axis=0)))},
        "layers": [],
        "warnings": check_order(stack),
    }

    start, out = (0, image)
    if source:
        start, resumed = cache.resume_from(source, stack)
        if resumed is not None:
            out = resumed
    facts["resumed_after"] = start

    for index, entry in enumerate(stack):
        key = entry.get("layer")
        spec = REGISTRY.get(key)
        record: dict[str, Any] = {"id": entry.get("id") or key, "layer": key}

        if index < start:
            # Already in the snapshot this run resumed from.
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

        cfg = {**spec.defaults(), **(entry.get("config") or {})}
        try:
            out = spec.apply(out, cfg, facts)
        except Exception as e:                       # noqa: BLE001
            # An interactive editor should say which layer is unhappy, not go
            # blank. The image continues unchanged.
            record["error"] = f"{type(e).__name__}: {e}"
        facts["layers"].append(record)
        if source:
            cache.remember(source, stack, index + 1, out)

    rgb = out[..., :3].reshape(-1, 3)
    facts["after"] = {"width": int(out.shape[1]), "height": int(out.shape[0]),
                      "colours": int(len(np.unique(rgb, axis=0)))}
    facts.pop("root", None)
    return out, facts
