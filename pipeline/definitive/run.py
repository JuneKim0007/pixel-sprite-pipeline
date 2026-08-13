"""Running a layer stack: prepare what is orderless, then walk forward.

Two halves, and the split between them is the whole design.

    prepare   For each layer, work out everything that is a function of the
              image reaching it and that layer's own settings, and of nothing
              else. Block size. Lattice phase. A generated palette. These are
              pure, so they are cached by content, and an edit that does not
              reach a layer never recomputes its preparation.

    apply     Walk forward. Each step takes the image and the answers prepared
              for it, and does the cheap ordered thing. Nothing here measures,
              searches or clusters; if it did, it would be doing orderless work
              inside the one pass that cannot skip any of it.

Before this the two were tangled: layers reached into the cache module from
inside their own bodies, so which work was orderless was invisible from the
outside and the runner could not skip, batch or report any of it. Separating
them makes three things possible that were not:

  - the expensive half is cached by content, so the same picture with the same
    settings never clusters twice
  - the forward half is cheap enough to run whole, so a change late in the
    stack does not depend on a snapshot to feel fast
  - `facts` can say what was computed and what was reused, which is the
    difference between a preview you trust and one you wait on

Preparation is interleaved rather than done up front, and that is forced: what
Grid prepares depends on the image Curves produced. What makes separating it
worthwhile anyway is the cache key - the arriving image plus that layer's own
settings - so a preparation is skipped whenever those match, no matter what
else in the stack moved.

Two behaviours worth stating because the loop does not show them.

A layer that raises does not kill the run. The editor is interactive, and a
half-typed hex colour should show an error against that layer rather than
blanking the preview. The image passes through unchanged and the failure is
reported in `facts`.

Alpha travels with the image. `background` produces a fourth channel and
everything after it has to preserve it, so layers receive whatever the previous
one returned rather than a normalised RGB array.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import cache
from .layers import REGISTRY, check_order


def prepare_for(spec, image: np.ndarray, cfg: dict, *, use_cache: bool = True) -> dict:
    """The orderless half of one layer, from cache when the inputs match.

    Keyed on the content of the arriving image and this layer's settings, and
    on nothing else - which is exactly the claim that makes it orderless. Two
    stacks differing anywhere after this layer still get the same answer here.
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

    `source` opts into resuming from a snapshot of the image partway through.
    Moving one slider changes one layer, and every layer before it is unchanged
    by definition. Passing no `source` runs the whole stack, which is what a
    pipeline stage wants: it runs once, on an image nothing will ask about
    again, and keeping snapshots for it would evict the editor's for nothing.
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

    for index, entry in enumerate(stack):
        key = entry.get("layer")
        spec = REGISTRY.get(key)
        record: dict[str, Any] = {"id": entry.get("id") or key, "layer": key}

        if index < start:
            record["cached"] = True                 # in the snapshot resumed from
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
        except Exception as e:                       # noqa: BLE001
            record["error"] = f"{type(e).__name__}: {e}"
        facts["layers"].append(record)
        if source:
            cache.remember(source, stack, index + 1, out)

    # Scale records the count before it magnifies, because magnification
    # cannot change it and counting afterwards is 17x the work for the same
    # number.
    facts["after"] = {"width": int(out.shape[1]), "height": int(out.shape[0]),
                      "colours": facts.pop("_colours", None)
                      if facts.get("_colours") is not None
                      else cache.count_colours(out)}
    facts.pop("root", None)
    return out, facts
