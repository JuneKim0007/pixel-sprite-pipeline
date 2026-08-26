"""Where a stage's declared needs are resolved, and the only place they are.

A stage says `needs = frozenset({"rig"})` and reads `ctx.need("rig")`. Nothing
in `stage.py` knows what a rig is, and `runner.validate` refuses a need no
resolver can answer before any GPU work starts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..geometry import annotate as ann
from ..geometry import rigs as rigs_mod
from ..refs import detect
from ..refs import references as refs_mod
from ..shared import settings as settings_mod


def _references(ctx) -> Any:
    """The typed reference library for this run."""
    cfg = ctx.settings("references")
    cfg.setdefault("_name", ctx.settings("name") or "")
    cfg.setdefault("_runs_dir", str(settings_mod.resolve_dir(
        ctx.root, (ctx.config.get("paths") or {}).get("output_dir"),
        "out/runs")))
    lib = refs_mod.load(ctx.root, cfg)

    for path in cfg.get("style_exemplars") or []:
        lib.style.append(refs_mod.Reference(
            path=Path(path), role="style", label="exemplar"))
    return lib


def measured_proportions(ctx) -> dict[str, float]:
    """Limb ratios inferred from annotated references, if any and enabled."""
    if ctx.settings("annotate") == "skip":
        return {}

    merged: dict[str, list[float]] = {}
    for a in ann.gather(ctx.need("references")):
        for group, factor in ann.infer_proportions(a).items():
            merged.setdefault(group, []).append(factor)
    return {group: round(sorted(values)[len(values) // 2], 2)
            for group, values in merged.items()}


def _detected(ctx) -> dict:
    """The rig and the record of how it was chosen, which are one answer. Splitting them into two resolvers would detect twice, and under `rig: auto` that is a second LLM call."""
    if "rig" not in ctx.resources:
        rig, record = detect.resolve(ctx.config, ctx.need("references"))
        measured = measured_proportions(ctx)
        if measured:
            record["measured_proportions"] = measured
        proportions = {**measured, **ctx.settings("proportions")}
        ctx.resources["rig"] = rigs_mod.scale(rig, proportions)
        ctx.resources["rig_record"] = record
    return ctx.resources


RESOLVERS: dict[str, Callable[[Any], Any]] = {
    "references": _references,
    "rig": lambda ctx: _detected(ctx)["rig"],
    "rig_record": lambda ctx: _detected(ctx)["rig_record"],
}

