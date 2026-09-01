"""Runs: what was produced, and starting another."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..generation import comfy, runner
from ..shared.errors import NotFound
from .context import CONFIGS, ROOT, runs_dir
from .contracts import Shape
from .routing import BaseRouter, get, post
from ..generation import schema
from datetime import datetime
import yaml
import re

_ACTIVE: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()


def validate_order(cfg: dict) -> str | None:
    """Return a human-readable problem with pipeline.stages, or None."""
    order = ((cfg or {}).get("pipeline") or {}).get("stages") or []
    try:
        runner.validate(runner.build(list(order)), seeded=set())
    except Exception as e:
        return str(e)
    return None


def _completed(run: Path) -> list[str]:
    """The stages a run recorded finishing; an unreadable manifest reads as none."""
    manifest = run / "artifacts.json"
    if not manifest.exists():
        return []
    try:
        return json.loads(manifest.read_text()).get("completed", [])
    except (OSError, json.JSONDecodeError):
        return []


def _stopped_at(run: Path, completed: list[str]) -> str | None:
    """The gate a run stopped on, or None if it ran past it or never reached it."""
    cfg_path = run / "config.yaml"
    if not cfg_path.exists():
        return None
    try:
        pipeline = ((yaml.safe_load(cfg_path.read_text()) or {}).get("pipeline")
                    or {})
    except (OSError, yaml.YAMLError):
        return None

    gate = pipeline.get("stop_after")
    if not gate or gate not in completed:
        return None
    planned = pipeline.get("stages") or []
    return gate if any(s not in completed for s in planned) else None


def list_runs() -> list[dict]:
    base = runs_dir()
    if not base.exists():
        return []
    out = []
    for d in sorted(base.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        stage_dirs = sorted(
            s for s in d.iterdir() if s.is_dir() and re.match(r"^\d\d_", s.name)
        )
        with _LOCK:
            running = d.name in _ACTIVE and _ACTIVE[d.name].poll() is None

        completed = _completed(d)
        stopped_at = _stopped_at(d, completed)

        out.append(
            {
                "id": d.name,
                "modified": datetime.fromtimestamp(d.stat().st_mtime).isoformat(
                    timespec="seconds"
                ),
                "running": running,
                "completed": completed,
                "stopped_at": stopped_at,
                "stages": [
                    {
                        "name": s.name.split("_", 1)[1],
                        "dir": s.name,
                        "images": sorted(p.name for p in s.glob("*.png")),
                    }
                    for s in stage_dirs
                ],
                "audit": run_audit(d),
            }
        )
    return out


def run_audit(run_dir: Path) -> dict:

    cfg_path = run_dir / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except yaml.YAMLError as e:
        return {"error": str(e)}

    refs = cfg.get("references") or {}
    contexts = {role: len(refs.get(role) or []) for role in
                ("identity", "style", "pose", "palette")}
    contexts["style_exemplars"] = len(refs.get("style_exemplars") or [])

    models = cfg.get("models") or {}
    canonical = cfg.get("canonical") or {}
    palette = cfg.get("palette") or {}

    pose_cfg = cfg.get("pose") or {}
    rig_source = pose_cfg.get("source", "library")
    annotated = None
    for meta in sorted(run_dir.glob("*_pose/pose.json")):
        try:
            data = json.loads(meta.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        first = (data.get("entries") or [{}])[0]
        annotated = first.get("from_annotation")
        break

    return {
        "protocol": cfg.get("module", "animation"),
        "rig_source": rig_source,
        "annotated_from": Path(annotated).name if annotated else "",
        "proportions": cfg.get("proportions") or {},
        "pose_fill": pose_cfg.get("fill"),
        "subject": cfg.get("subject", ""),
        "styles": list(cfg.get("styles") or []),
        "rig": cfg.get("rig", ""),
        "stages": list((cfg.get("pipeline") or {}).get("stages") or []),
        "contexts": contexts,
        "context_total": sum(v for k, v in contexts.items() if k != "style_exemplars"),
        "models": {
            "checkpoint": comfy.model_name(models, "checkpoint"),
            "vae": comfy.model_name(models, "vae"),
            "lora": comfy.model_name(models, "pixel_lora"),
        },
        "seed": canonical.get("seed"),
        "steps": canonical.get("steps"),
        "palette": {"source": palette.get("source"), "size": palette.get("size"),
                    "factor": palette.get("factor"), "match": palette.get("match")},
    }


def run_detail(run_id: str) -> dict:
    d = runs_dir() / run_id
    if not d.is_dir():
        raise NotFound("run", run_id)
    info = next((r for r in list_runs() if r["id"] == run_id), {"id": run_id})
    log = d / "run.log"
    info["log"] = log.read_text(errors="replace") if log.exists() else ""
    cfg = d / "config.yaml"
    info["config"] = cfg.read_text() if cfg.exists() else ""
    info["dir"] = str(d)
    info["audit"] = run_audit(d)
    return info


def start_run(config_name: str, overrides: dict | None, resume: str | None,
              style_picks: dict | None = None) -> str:
    cmd = [sys.executable, "-u", str(ROOT / "run.py")]

    if resume:
        out = runs_dir() / resume
        if not out.is_dir():
            raise NotFound("run", resume)
        run_id = resume
        cmd += ["--resume", resume]
        log_mode = "a"
    else:
        cfg_path = CONFIGS / f"{config_name}.yaml"
        if not cfg_path.exists():
            raise NotFound("config", config_name)
        run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{config_name}"
        out = runs_dir() / run_id
        out.mkdir(parents=True, exist_ok=True)

        effective = cfg_path
        if overrides or style_picks:
            merged = yaml.safe_load(cfg_path.read_text()) or {}
            for path, value in (overrides or {}).items():
                schema.set_path(merged, path, value)
            if style_picks:
                merged["style_picks"] = style_picks
            effective = out / "config.effective.yaml"
            effective.write_text(yaml.safe_dump(merged, sort_keys=False))
        cmd += [str(effective), "--run-id", run_id]
        log_mode = "w"

    log = (out / "run.log").open(log_mode)
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    with _LOCK:
        _ACTIVE[run_id] = proc
    return run_id


class Runs(BaseRouter):
    prefix = "/api"

    @get("/runs", "every run, newest first", returns=Shape(runs=list))
    def index(self, req):
        return {"runs": list_runs()}

    @get("/run", "one run in detail, with an audit of what produced it",
         returns=Shape(id=str, dir=str, log=str, config=str, audit=dict))
    def detail(self, req):
        return run_detail(req.required("id"))

    @post("/run", "start a pipeline", returns=Shape(run_id=str))
    def start(self, req):
        run_id = start_run(req.get("config", ""), req.get("overrides") or {},
                           req.get("stages") or None, req.get("picks") or None)
        return {"run_id": run_id}

    @post("/stop", "stop a running pipeline", returns=Shape(stopped=str))
    def stop(self, req):
        rid = req.get("run_id", "")
        with _LOCK:
            proc = _ACTIVE.get(rid)
        if not proc:
            raise NotFound("running job", rid)
        proc.terminate()
        return {"stopped": rid}
