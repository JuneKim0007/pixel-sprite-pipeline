"""Everything wrong with a config that is knowable before the run starts."""

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


NO_STAGES = ("a config must define pipeline.stages, e.g.\n"
             "  pipeline:\n"
             "    stages: [pose, canonical, frames, palette, export]")


def stage_order(cfg: dict) -> str | None:
    """What is wrong with pipeline.stages, or None. An empty order is not judged here."""
    from .. import stages  # noqa: F401  (importing registers them)
    from ..generation import runner

    order = ((cfg or {}).get("pipeline") or {}).get("stages") or []
    try:
        runner.validate(runner.build(list(order)), seeded=set())
    except Exception as e:                       # noqa: BLE001
        return str(e)
    return None


def _stages(cfg: dict) -> list[str]:
    if not (cfg.get("pipeline") or {}).get("stages"):
        # Only the CLI refused this, in load_config.
        return [NO_STAGES]
    problem = stage_order(cfg)
    return [problem.split("\n")[0]] if problem else []


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
