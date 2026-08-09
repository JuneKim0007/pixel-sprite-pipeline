"""Pick a rig by looking at the reference images.

The task is classification — "which of these body plans is this?" — not
localization. That distinction is the whole reason this is worth doing: VLM
spatial localization benchmarks sit around 35% against 26% for random guessing,
so asking a model to place joints would be worse than useless, while choosing
among a fixed list of seventeen names is something they handle well and which
can be checked instantly against the registry.

The result is a proposal, never a commitment. It is written into the run with
its confidence so the pose gate can show it for confirmation — a wrong guess
should cost a click, not six GPU-minutes.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import rigs

SYSTEM = """You identify the body plan of a creature in reference images.
You output ONLY JSON. Judge anatomy, not art style or colour."""

TEMPLATE = """Which body plan do these images show?

{catalogue}

Answer with JSON exactly like:
{{"rig": "<one name from the list>", "confidence": 0.0-1.0, "why": "<short>"}}

Count limbs before answering. If the images disagree with each other, or a
limb is cropped out of frame, say so in "why" and lower the confidence."""


def catalogue() -> str:
    lines = []
    for summary in rigs.summaries():
        rig = rigs.get(summary["name"])
        lines.append(
            f"- {summary['name']}: {summary['label']}"
            + (f" ({rig.prompt_hint})" if rig.prompt_hint else "")
        )
    return "\n".join(lines)


def detect(
    images: list[Path],
    client,
    *,
    attempts: int = 3,
    verbose: bool = True,
) -> tuple[rigs.Rig, float, str]:
    """Return (rig, confidence, reason). Falls back to humanoid on failure."""
    if not images:
        raise ValueError("no reference images to inspect")

    prompt = TEMPLATE.format(catalogue=catalogue())
    last = ""

    for attempt in range(1, attempts + 1):
        raw = client.look(prompt, images, system=SYSTEM)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            last = "reply was not JSON"
            if verbose:
                print(f"   attempt {attempt}: {last}")
            continue

        name = str(data.get("rig", "")).strip()
        if name in rigs.REGISTRY:
            confidence = float(data.get("confidence", 0.0) or 0.0)
            why = str(data.get("why", ""))[:200]
            if verbose:
                print(f"   detected '{name}' ({confidence:.0%}) — {why}")
            return rigs.get(name), confidence, why

        last = f"'{name}' is not a known rig"
        if verbose:
            print(f"   attempt {attempt}: {last}")
        prompt += f"\n\n'{name}' is not in the list. Choose one of exactly these names."

    if verbose:
        print(f"   detection failed ({last}); falling back to humanoid")
    return rigs.get(rigs.DEFAULT), 0.0, f"detection failed: {last}"


def resolve(ctx, verbose: bool = True) -> tuple[rigs.Rig, dict]:
    """Config-driven entry point. `rig: auto` inspects the references.

    Returns the rig plus a record of how it was chosen, so a run can show its
    reasoning rather than silently posing a snake as a person.
    """
    requested = ctx.config.get("rig")
    if requested != "auto":
        return rigs.get(requested), {"source": "config", "rig": rigs.get(requested).name}

    lib = ctx.references()
    refs = lib.identity or lib.pose
    if not refs:
        raise ValueError(
            "rig is 'auto' but there are no reference images to look at. "
            "Add references, or name a rig explicitly."
        )

    from .llm import Ollama

    llm_cfg = (ctx.config.get("detect") or {})
    client = Ollama(
        host=llm_cfg.get("host", "http://127.0.0.1:11434"),
        model=llm_cfg.get("model", "qwen2.5vl:3b"),
        keep_alive=llm_cfg.get("keep_alive", 0),
    )
    if not client.alive():
        raise RuntimeError(
            "rig is 'auto' but Ollama is not running. Start it, or name a rig."
        )

    rig, confidence, why = detect(
        [r.path for r in refs], client,
        attempts=int(llm_cfg.get("attempts", 3)), verbose=verbose,
    )
    floor = float(llm_cfg.get("min_confidence", 0.0))
    if confidence < floor:
        if verbose:
            print(f"   confidence {confidence:.0%} below {floor:.0%}; using humanoid")
        rig = rigs.get(rigs.DEFAULT)

    return rig, {
        "source": "vision",
        "rig": rig.name,
        "confidence": confidence,
        "why": why,
        "model": client.model,
        "images": [str(r.path) for r in refs],
    }
