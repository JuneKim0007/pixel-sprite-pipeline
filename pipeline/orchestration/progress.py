"""How much of a run is left, derived rather than counted by hand."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# What one stage submits to the GPU, and what it writes.
GPU_STAGES = ("canonical", "frames", "softbody")


def _entries(run: Path) -> int:
    """Pose entries this run actually has, which decides the frame count."""
    for meta in sorted(run.glob("*_pose/pose.json")):
        try:
            return len(json.loads(meta.read_text()).get("entries") or [])
        except (OSError, json.JSONDecodeError):
            return 0
    return 0


def planned(config: dict, run: Path) -> dict[str, dict[str, int]]:
    """Per stage: GPU jobs it will submit, and images it will write."""
    canonical = config.get("canonical") or {}
    candidates = max(1, int(canonical.get("candidates") or 1))
    batched = bool(canonical.get("batch_candidates", True)) and candidates > 1

    entries = _entries(run)
    views = entries or 1

    out: dict[str, dict[str, int]] = {
        # One job per view when candidates batch, otherwise one per candidate.
        "canonical": {"jobs": views if batched else views * candidates,
                      "images": views * candidates},
        "frames": {"jobs": entries, "images": entries},
    }
    stages = ((config.get("pipeline") or {}).get("stages") or [])
    return {k: v for k, v in out.items() if k in stages}


def _written(run: Path, stage: str) -> int:
    for found in sorted(run.glob(f"*_{stage}")):
        return len(list(found.glob("*.png")))
    return 0


def _on_disk(run: Path) -> list[str]:
    """Stages that have written a directory, in order."""
    return [d.name.split("_", 1)[1] for d in sorted(run.glob("[0-9][0-9]_*"))
            if d.is_dir()]


def of_run(run: Path, config: dict, completed: list[str]) -> dict[str, Any]:
    """What this run has produced against what it set out to."""
    plan = planned(config, run)
    stages = (config.get("pipeline") or {}).get("stages") or []
    completed = sorted(set(completed) | set(_on_disk(run)),
                       key=lambda s: stages.index(s) if s in stages else 99)

    jobs_total = sum(v["jobs"] for v in plan.values())
    images_total = sum(v["images"] for v in plan.values())
    images_made = sum(_written(run, name) for name in plan)

    jobs_done = 0
    for name, want in plan.items():
        made = _written(run, name)
        if want["images"]:
            jobs_done += round(want["jobs"] * min(1.0, made / want["images"]))

    return {
        "stages": {"total": len(stages), "done": len(completed)},
        "jobs": {"total": jobs_total, "done": jobs_done},
        "images": {"total": images_total, "made": images_made},
        "per_stage": plan,
    }
