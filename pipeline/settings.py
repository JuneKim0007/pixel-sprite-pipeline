"""Global defaults, per-pipeline overrides, and the merge between them.

A pipeline config now answers two different questions, and they change at
different rates:

  "how does this machine behave"   compute, model paths, service hosts,
                                   working directories — set once, true for
                                   every pipeline
  "what am I making"               subject, poses, strengths — per asset

Keeping both in every config means re-setting the machine answers in each new
pipeline, and changing one means editing every file. So machine-level values
live in configs/_global.yaml and a pipeline config only carries what it
deliberately differs on.

The merge is deep and the pipeline always wins. A value present in the pipeline
config is an override even if it happens to equal the global — the UI needs to
distinguish "inherited" from "set to the same thing", or a Reset button cannot
know what to remove.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

GLOBAL_NAME = "_global"

# Shipped defaults for the machine-level half. Written on first run so the file
# exists to be edited rather than appearing by magic later.
DEFAULT_GLOBAL: dict[str, Any] = {
    "paths": {
        # Where reference images and other inputs are read from and uploaded to.
        "input_dir": "inputs",
        # Where runs are written. Blank means out/runs under the project.
        "output_dir": "out/runs",
        # Default target for Download in the UI.
        "download_dir": "exports",
    },
    "comfy": {"host": "http://127.0.0.1:8188"},
    "compute": {
        "torch_threads": 8,
        "mps_high_watermark": 0.9,
        "cpu_workers": None,
        "batch": 1,
    },
    "models": {
        "checkpoint": "sd_xl_base_1.0.safetensors",
        "pixel_lora": "pixel-art-xl.safetensors",
        "controlnet": "controlnet-union-sdxl-promax.safetensors",
        "ipadapter": "ip-adapter_sdxl_vit-h.safetensors",
    },
    "ui": {
        # Remembered "don't show me this again" acknowledgements.
        "suppress_gate_confirm": False,
        "suppress_overwrite_confirm": False,
    },
}


def global_path(root: Path) -> Path:
    return root / "configs" / f"{GLOBAL_NAME}.yaml"


def load_global(root: Path) -> dict[str, Any]:
    path = global_path(root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Machine-level defaults. Every pipeline inherits these unless it\n"
            "# overrides them. Edit here to change all pipelines at once.\n"
            + yaml.safe_dump(DEFAULT_GLOBAL, sort_keys=False)
        )
    loaded = yaml.safe_load(path.read_text()) or {}
    return deep_merge(copy.deepcopy(DEFAULT_GLOBAL), loaded)


def deep_merge(base: dict, over: dict) -> dict:
    """Recursive merge; `over` wins. Lists replace rather than concatenate.

    Concatenating would make it impossible to shorten a list in an override —
    you could add a reference image but never remove one.
    """
    out = dict(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def effective(root: Path, pipeline_cfg: dict) -> dict:
    """What the pipeline actually runs with: global defaults under the config."""
    return deep_merge(load_global(root), pipeline_cfg or {})


def overridden_paths(pipeline_cfg: dict, prefix: str = "") -> set[str]:
    """Dotted paths the pipeline config sets, whatever their value.

    Presence is the override, not difference. A pipeline that sets cfg 7.0 when
    the global is also 7.0 is still pinned to 7.0 — so Reset must remove the
    key, and the UI must be able to show it as pinned.
    """
    out: set[str] = set()
    for key, value in (pipeline_cfg or {}).items():
        here = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            nested = overridden_paths(value, here)
            out |= nested or {here}
        else:
            out.add(here)
    return out


def unset(cfg: dict, dotted: str) -> bool:
    """Remove an override so the value falls back to global. True if removed."""
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            return False
        node = nxt
    if parts[-1] in node:
        del node[parts[-1]]
        return True
    return False


def resolve_dir(root: Path, value: str | None, fallback: str) -> Path:
    """Turn a configured directory into an absolute path, creating it."""
    raw = (value or fallback).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
