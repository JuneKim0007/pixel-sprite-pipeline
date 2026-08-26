
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from ..generation import schema
from ..shared import settings
from .. import stages  # noqa: F401  (importing registers the stages)
from ..generation.stage import available
from .context import (CONFIGS, ROOT, dir_size, download_dir, global_cfg,
                      human_size, input_dir, runs_dir)
from .contracts import Shape
from .routing import BaseRouter, get
import sys
import os


def system_info() -> dict:
    """Installed weights and service health, for the About / Settings view."""
    model_root = ROOT / "ComfyUI" / "models"
    groups = {
        "checkpoints": "Base diffusion weights",
        "loras": "Style / speed adapters",
        "controlnet": "Structural conditioning",
        "ipadapter": "Identity conditioning",
        "clip_vision": "Image encoder for IP-Adapter",
        "vae": "Latent decoder",
    }
    weights = []
    for folder, purpose in groups.items():
        d = model_root / folder
        if not d.exists():
            continue
        for f in sorted(d.glob("*.safetensors")):
            weights.append(
                {
                    "group": folder, "purpose": purpose, "name": f.name,
                    "size": human_size(f.stat().st_size),
                }
            )

    services = {}
    try:
        from pipeline.generation.comfy import Client

        c = Client()
        services["comfyui"] = {"url": c.host, "up": c.alive()}
    except Exception as e:  # pragma: no cover
        services["comfyui"] = {"up": False, "error": str(e)}
    try:
        from pipeline.refs.llm import Ollama

        o = Ollama()
        up = o.alive()
        services["ollama"] = {"url": o.host, "up": up, "models": o.models() if up else []}
    except Exception as e:  # pragma: no cover
        services["ollama"] = {"up": False, "error": str(e)}

    total, used, free = shutil.disk_usage(ROOT)
    return {
        "weights": weights,
        "services": services,
        "paths": {
            "root": str(ROOT),
            "input_dir": str(input_dir()),
            "output_dir": str(runs_dir()),
            "download_dir": str(download_dir()),
        },
        "host": {
            "platform": sys.platform,
            "cpu_count": os.cpu_count(),
            "python": sys.version.split()[0],
            "disk_free": human_size(free),
            "models_size": human_size(dir_size(model_root)) if model_root.exists() else "0",
        },
        "compute_note": (
            "Metal exposes no way to partition the GPU between processes — "
            "there is no equivalent of CUDA_VISIBLE_DEVICES or MIG. GPU load is "
            "controlled by how much work you send it (steps, resolution, batch, "
            "model size) and by the memory ceiling, not by a core count."
        ),
    }


class Machine(BaseRouter):
    prefix = "/api"

    @get("/system", "services, paths, disk and models",
         returns=Shape(services=dict, paths=dict, host=dict, weights=list,
                       compute_note=str))
    def system(self, req):
        return system_info()

    @get("/schema", "every configurable field for a module",
         returns=Shape(module=str, modules=dict, fields=list, options=dict,
                       stages=list, resources=list))
    def schema(self, req):
        return schema.describe(ROOT, req.query("module") or None)
