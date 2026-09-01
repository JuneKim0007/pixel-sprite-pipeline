"""Global defaults, per-pipeline overrides, and the merge between them."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from . import paths

GLOBAL_NAME = "_global"

DEFAULT_GLOBAL: dict[str, Any] = {
    "paths": {
        "input_dir": "library/refs",
        "output_dir": "out/runs",
        "download_dir": "out/exports",
    },
    "compute": {
        "torch_threads": 8,
        "mps_high_watermark": 0.9,
        "cpu_workers": None,
        "batch": 1,
    },
    "models": {
        "checkpoint": "sd_xl_base_1.0.safetensors",
        "pixel_lora": "pixel-art-xl.safetensors",
        "lcm_lora": "lcm-lora-sdxl.safetensors",
        "vae": "sdxl_vae_fp16fix.safetensors",
        "controlnet": "controlnet-union-sdxl-promax.safetensors",
        "ipadapter": "ip-adapter_sdxl_vit-h.safetensors",
        "clip_vision": "CLIP-ViT-H-14.safetensors",
    },
    "ui": {
        "suppress_gate_confirm": False,
        "suppress_overwrite_confirm": False,
    },
}


def read_yaml(path: Path) -> dict:
    """A YAML file as a dict. An empty file is an empty config, not None."""
    return yaml.safe_load(path.read_text()) or {}


def global_path(root: Path) -> Path:
    return paths.resolve(root, "configs") / f"{GLOBAL_NAME}.yaml"


def load_global(root: Path) -> dict[str, Any]:
    path = global_path(root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Machine-level defaults. Every pipeline inherits these unless it\n"
            "# overrides them. Edit here to change all pipelines at once.\n"
            + yaml.safe_dump(DEFAULT_GLOBAL, sort_keys=False)
        )
    loaded = read_yaml(path)
    return deep_merge(copy.deepcopy(DEFAULT_GLOBAL), loaded)


def deep_merge(base: dict, over: dict) -> dict:
    """Concatenating would make it impossible to shorten a list in an override — you could add a reference image but never remove one."""
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
    """A pipeline that sets cfg 7.0 when the global is also 7.0 is still pinned to 7.0 — so Reset must remove the key, and the UI must be able to show it as pinned."""
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
