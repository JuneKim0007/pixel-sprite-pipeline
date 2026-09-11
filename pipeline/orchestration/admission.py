"""Everything wrong with a config that is knowable before the run starts.

One answer for three callers. `run.py` refuses, `api.runs.start_run` refuses,
and `queue.preflight` reports - and until 2026-09-11 each checked a different
subset, so a job the queue would not take was one the Run button started.
"""

from __future__ import annotations

from pathlib import Path


def _settings(cfg: dict) -> list[str]:
    from ..generation.schema import SCHEMA
    from ..shared.errors import Invalid

    try:
        SCHEMA.check(cfg)
    except Invalid as e:
        return ["\n".join(filter(None, [e.message, e.hint]))]
    return []


def _stages(cfg: dict) -> list[str]:
    from .. import stages  # noqa: F401  (importing registers them)
    from ..generation import runner

    order = (cfg.get("pipeline") or {}).get("stages") or []
    try:
        runner.validate(runner.build(list(order)), seeded=set())
    except Exception as e:                       # noqa: BLE001
        return [str(e).split("\n")[0]]
    return []


def _rig(cfg: dict) -> list[str]:
    rig = cfg.get("rig")
    if not rig or rig == "auto":
        return []
    from ..geometry import rigs

    return [] if rig in rigs.REGISTRY else [f"unknown rig '{rig}'"]


def _references(root: Path, cfg: dict) -> list[str]:
    from ..refs import references as refs_mod

    ref_cfg = cfg.get("references") or {}
    found = [refs_mod.IMAGES_REPLACED] if "images" in ref_cfg else []
    return found + refs_mod.unresolved(root, ref_cfg)


def problems(root: Path, cfg: dict) -> list[str]:
    """Named in the order a person fixes them: the setting, then what it names."""
    return (_settings(cfg) + _stages(cfg) + _rig(cfg)
            + _references(root, cfg))
