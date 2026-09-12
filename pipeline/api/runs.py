"""Runs: what was produced, and starting another."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import stages as _stages  # noqa: F401  - registers them
from ..generation import comfy, runner
from ..shared import guard, settings
from ..orchestration import launch
from ..shared.errors import Conflict, NotFound
from .context import CONFIGS, ROOT, runs_dir
from .contracts import Shape
from .routing import BaseRouter, get, post
from ..generation import schema
from datetime import datetime
import yaml
import re

_ACTIVE: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()


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
        pipeline = (settings.read_yaml(cfg_path).get("pipeline") or {})
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
    # One source of truth, and the only one that survives a restart.
    live = _in_flight()
    out = []
    for d in sorted(base.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        stage_dirs = sorted(
            s for s in d.iterdir() if s.is_dir() and re.match(r"^\d\d_", s.name)
        )
        running = d.name == live

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
        cfg = settings.read_yaml(cfg_path)
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
        "protocol": cfg.get("module", settings.DEFAULT_MODULE),
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
        "score": _score(run_dir),
    }


def _score(run: Path) -> dict:
    """What a run measured against its reference, if anything scored it."""
    path = run / "score.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _consumed(run: Path, stage_names: list[str]) -> dict[str, list[str]]:
    """What each stage was handed, from the declared graph and the manifest."""
    try:
        stages = runner.build(list(stage_names))
    except Exception:                                   # noqa: BLE001
        return {}

    manifest = run / "artifacts.json"
    if not manifest.exists():
        return {}
    try:
        stored = json.loads(manifest.read_text()).get("artifacts", {})
    except (OSError, json.JSONDecodeError):
        return {}

    paths = {k: v.get("value") for k, v in stored.items()
             if isinstance(v, dict) and v.get("type") == "paths"}

    out: dict[str, list[str]] = {}
    for spec in stages:
        got = []
        for need in sorted(spec.needs):
            for one in paths.get(need) or []:
                got.append(str(one))
        if got:
            out[spec.name] = got
    return out


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
    info["consumed"] = _consumed(d, info["audit"].get("stages") or [])
    info["shown"] = _shown(d)
    return info


def _shown(run_dir: Path) -> dict:
    """The references and rig this run was given, kept beside what it made."""
    import json

    for folder in sorted(run_dir.glob("*/references")):
        record = folder / "used.json"
        if not record.is_file():
            continue
        try:
            said = json.loads(record.read_text())
        except (OSError, ValueError):
            continue
        for row in said.get("references", []):
            # `kept` is written relative to the project root.
            row["missing"] = not (ROOT / row.get("kept", "")).is_file()
        # The guide drawn from the rig, so the two can be laid over each other.
        guides = sorted(run_dir.glob("*_pose/skeleton_*.png"))
        said["guides"] = [str(g.relative_to(ROOT)) for g in guides]
        return said
    return {}


def _in_flight() -> str | None:
    """The run already going, whether this process started it or not."""
    with _LOCK:
        for run_id, proc in list(_ACTIVE.items()):
            if proc.poll() is None:
                return run_id
            del _ACTIVE[run_id]
    return guard.run_in_flight()


def run_progress(run_id: str = "") -> dict:
    """Two answers: what the GPU is doing now, and how far the run has come."""
    from ..generation import comfy
    from ..orchestration import progress as progress_mod

    host = (settings.load_global(ROOT).get("comfy") or {}).get(
        "host", "http://127.0.0.1:8188")
    remaining = comfy.Client(host).pending()
    gpu = {"reachable": remaining is not None,
           "queue_remaining": remaining or 0,
           "busy": bool(remaining)}

    run_id = run_id or guard.run_in_flight() or ""
    out = {"gpu": gpu, "run": {"id": run_id}}
    if not run_id:
        return out

    d = runs_dir() / run_id
    if not d.is_dir():
        return out

    cfg_path = d / "config.yaml"
    cfg = settings.read_yaml(cfg_path) if cfg_path.exists() else {}
    out["run"] = {"id": run_id, "running": guard.run_in_flight() == run_id,
                  **progress_mod.of_run(d, cfg, _completed(d))}
    return out


def start_run(config_name: str, overrides: dict | None, resume: str | None,
              style_picks: dict | None = None) -> str:
    # One at a time.
    busy = _in_flight()
    if busy:
        raise Conflict(
            f"'{busy}' is still running.",
            hint="Stop it first, or wait for it to finish." if busy != resume
                 else "It is already running; watch it on the Result tab.")

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
        # The same preparation the CLI and autopilot do.
        ready = launch.prepare(
            ROOT, cfg_path, overrides=overrides, style_picks=style_picks,
            run_id=f"{time.strftime('%Y%m%d_%H%M%S')}_{config_name}")
        run_id, out = ready.run_id, ready.outdir
        cmd += [str(ready.config_path), "--run-id", run_id,
                "--outdir", str(out.parent)]
        log_mode = "w"

    log = (out / "run.log").open(log_mode)
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    guard.GUARD.watch(proc.pid, f"run:{run_id}")
    with _LOCK:
        _ACTIVE[run_id] = proc
    return run_id


class Runs(BaseRouter):
    prefix = "/api"

    @get("/runs", "every run, newest first", returns=Shape(runs=list))
    def index(self, req):
        return {"runs": list_runs()}

    @get("/progress", "how far the machine is through what it was asked for",
         returns=Shape(gpu=dict, run=dict))
    def progress(self, req):
        return run_progress(req.query("id", ""))

    @get("/run", "one run in detail, with an audit of what produced it",
         returns=Shape(id=str, dir=str, log=str, config=str, audit=dict))
    def detail(self, req):
        return run_detail(req.required("id"))

    @post("/run", "start a pipeline", returns=Shape(run_id=str))
    def start(self, req):
        run_id = start_run(req.get("config", ""), req.get("overrides") or {},
                           req.get("resume") or None, req.get("picks") or None)
        return {"run_id": run_id}

    @get("/housekeeping", "what each scope would remove",
         returns=Shape(scopes=list))
    def housekeeping(self, req):
        from ..orchestration import housekeeping as hk

        found = hk.counts(ROOT)
        return {"scopes": [{"name": name, "label": s.label, "note": s.note,
                            "count": found.get(name, 0)}
                           for name, s in hk.SCOPES.items()]}

    @post("/housekeeping", "remove the named scopes", returns=Shape(removed=dict))
    def wipe(self, req):
        from ..orchestration import housekeeping as hk

        # A wipe while a run is writing removes the directory underneath it and
        # leaves a manifest describing files that are gone.
        live = _in_flight()
        if live:
            raise Conflict(f"'{live}' is still running.",
                           hint="Stop it first; a wipe would delete what it is "
                                "writing.")
        return {"removed": hk.wipe(ROOT, list(req.get("scopes") or []))}

    @post("/stop", "stop a running pipeline", returns=Shape(stopped=str))
    def stop(self, req):
        rid = req.get("run_id", "") or guard.run_in_flight() or ""
        with _LOCK:
            proc = _ACTIVE.get(rid)
        if proc and proc.poll() is None:
            proc.terminate()
            return {"stopped": rid}
        # _ACTIVE is this process's memory of what it started.
        if not guard.stop_run(rid):
            raise NotFound("running job", rid)
        return {"stopped": rid}
