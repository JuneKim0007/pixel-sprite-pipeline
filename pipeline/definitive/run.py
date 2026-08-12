"""Executing a layer stack.

The whole of it is a loop, which is the point: the interesting decisions moved
into the stack itself, so running one has nothing left to decide.

Two behaviours are worth stating because they are not obvious from the loop.

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

from .layers import REGISTRY, check_order


def apply_stack(image: np.ndarray, stack: list[dict], *,
                root: Path | None = None) -> tuple[np.ndarray, dict]:
    """Run every enabled layer in order. Returns the image and what was measured."""
    facts: dict[str, Any] = {
        "root": root,
        "before": {"width": int(image.shape[1]), "height": int(image.shape[0]),
                   "colours": int(len(np.unique(image.reshape(-1, image.shape[2])[:, :3],
                                                axis=0)))},
        "layers": [],
        "warnings": check_order(stack),
    }

    out = image
    for entry in stack:
        key = entry.get("layer")
        spec = REGISTRY.get(key)
        record: dict[str, Any] = {"id": entry.get("id") or key, "layer": key}

        if spec is None:
            record["error"] = f"no layer '{key}'"
            facts["layers"].append(record)
            continue
        if not entry.get("enabled", True):
            record["skipped"] = True
            facts["layers"].append(record)
            continue

        cfg = {**spec.defaults(), **(entry.get("config") or {})}
        try:
            out = spec.apply(out, cfg, facts)
        except Exception as e:                       # noqa: BLE001
            # An interactive editor should say which layer is unhappy, not go
            # blank. The image continues unchanged.
            record["error"] = f"{type(e).__name__}: {e}"
        facts["layers"].append(record)

    rgb = out[..., :3].reshape(-1, 3)
    facts["after"] = {"width": int(out.shape[1]), "height": int(out.shape[0]),
                      "colours": int(len(np.unique(rgb, axis=0)))}
    facts.pop("root", None)
    return out, facts
