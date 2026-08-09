#!/usr/bin/env python3
"""Local web UI for the sprite pipeline.

    python server.py            # http://127.0.0.1:8000

Why a server and not a static page: the browser cannot read your directories,
edit config files, or start a run. Those are the whole point of the interface,
so there is a small backend — stdlib plus the YAML libraries already in use,
no framework. It reuses the pipeline package directly rather than duplicating
any knowledge of stages or settings.

Binds to loopback only. Nothing here is authenticated, so do not expose it.
"""

from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import ruamel.yaml
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline import artifacts as artifacts_io  # noqa: E402
from pipeline import files as files_mod  # noqa: E402
from pipeline import runner, schema, settings, stages, styles  # noqa: E402,F401
from pipeline.stage import available  # noqa: E402

STATIC = ROOT / "web"
CONFIGS = ROOT / "configs"

# run_id -> Popen, so a run can be polled and stopped.
_ACTIVE: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()

# Round-trip loader. The configs carry their documentation in comments, and a
# plain safe_load/safe_dump cycle silently deletes all of it — so a single Save
# from the UI would strip the explanation of every knob it just edited.
_RT = ruamel.yaml.YAML()
_RT.preserve_quotes = True
_RT.width = 4096


# --------------------------------------------------------------------- helpers


def load_roundtrip(path: Path):
    with path.open() as fh:
        return _RT.load(fh)


def dump_roundtrip(data, path: Path) -> None:
    with path.open("w") as fh:
        _RT.dump(data, fh)


def global_cfg() -> dict:
    return settings.load_global(ROOT)


def runs_dir() -> Path:
    return settings.resolve_dir(
        ROOT, (global_cfg().get("paths") or {}).get("output_dir"), "out/runs"
    )


def input_dir() -> Path:
    return settings.resolve_dir(
        ROOT, (global_cfg().get("paths") or {}).get("input_dir"), "inputs"
    )


def download_dir() -> Path:
    return settings.resolve_dir(
        ROOT, (global_cfg().get("paths") or {}).get("download_dir"), "exports"
    )


def allowed_roots() -> list[Path]:
    """Directories the browser is permitted to read or write.

    The configured input/output/download directories may sit outside the
    project, so they are listed explicitly rather than assuming everything
    lives under ROOT.
    """
    return [ROOT, input_dir(), runs_dir(), download_dir(), Path.home()]


def human_size(n: int) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < step:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} PB"


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def validate_order(cfg: dict) -> str | None:
    """Return a human-readable problem with pipeline.stages, or None."""
    order = ((cfg or {}).get("pipeline") or {}).get("stages") or []
    try:
        runner.validate(runner.build(list(order)), seeded=set())
    except Exception as e:
        return str(e)
    return None


def _apply_changes(doc, incoming, path: str = "") -> int:
    """Copy differing leaves from `incoming` into the round-tripped `doc`.

    Writing the whole parsed document back would reformat everything and lose
    the comments; touching only what actually changed leaves the rest of the
    file byte-identical.
    """
    changed = 0
    for key, value in (incoming or {}).items():
        here = f"{path}.{key}" if path else key
        if isinstance(value, dict) and isinstance(doc.get(key), dict):
            changed += _apply_changes(doc[key], value, here)
        elif key not in doc or doc[key] != value:
            doc[key] = value
            changed += 1
    return changed


# ------------------------------------------------------------------ run state


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

        stopped_at, completed = None, []
        manifest = d / "artifacts.json"
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text())
                completed = data.get("completed", [])
            except (OSError, json.JSONDecodeError):
                pass
        cfg_path = d / "config.yaml"
        if cfg_path.exists():
            try:
                cfg = yaml.safe_load(cfg_path.read_text()) or {}
                gate = (cfg.get("pipeline") or {}).get("stop_after")
                if gate and gate in completed:
                    planned = (cfg.get("pipeline") or {}).get("stages") or []
                    if any(s not in completed for s in planned):
                        stopped_at = gate
            except (OSError, yaml.YAMLError):
                pass

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
            }
        )
    return out


def run_detail(run_id: str) -> dict:
    d = runs_dir() / run_id
    if not d.is_dir():
        raise FileNotFoundError(run_id)
    info = next((r for r in list_runs() if r["id"] == run_id), {"id": run_id})
    log = d / "run.log"
    info["log"] = log.read_text(errors="replace") if log.exists() else ""
    cfg = d / "config.yaml"
    info["config"] = cfg.read_text() if cfg.exists() else ""
    info["dir"] = str(d)
    return info


def _sheet(name: str) -> styles.Style:
    found = styles.discover(ROOT).get(name)
    if not found:
        raise FileNotFoundError(f"no style sheet '{name}'")
    return found


def style_detail(name: str) -> dict:
    """Everything the Styles tab shows for one sheet.

    Split deliberately into *context* — what is true now and is editable — and
    *history* — what happened and is not. They answer different questions, and
    a panel that mixes them makes the second one unreadable.
    """
    from pipeline import stylelog

    sheet = _sheet(name)
    images = []
    for path in sheet.exemplars:
        if not path.exists():
            images.append({"path": str(path), "name": path.name, "missing": True})
            continue
        images.append({
            "path": str(path), "name": path.name,
            "bytes": path.stat().st_size, "missing": False,
        })

    pending = sheet.training_images
    archives = []
    archive_root = sheet.home / "training" / "archive"
    if archive_root.is_dir():
        for folder in sorted(archive_root.iterdir(), reverse=True):
            if folder.is_dir():
                archives.append({
                    "name": folder.name,
                    "count": sum(1 for _ in folder.iterdir()),
                })

    return {
        "name": sheet.name,
        "label": sheet.label,
        "foldered": sheet.foldered,
        "home": str(sheet.home),
        "summary": sheet.summary(ROOT),
        "context": {
            # Two kinds, because they are edited differently: one is a folder
            # of files, the other is text in a document.
            "images": images,
            "prompts": {
                "vocabulary": sheet.vocabulary,
                "notes": sheet.notes,
                "token": sheet.token,
            },
        },
        "training": {
            "pending": len(pending),
            "pending_names": [p.name for p in pending[:24]],
            "archives": archives,
            "lora": sheet.lora,
        },
        "tuning": sheet.tuning,
        "history": stylelog.read(sheet.home),
    }


def add_style_note(name: str, text: str) -> dict:
    from pipeline import stylelog

    if not text.strip():
        raise ValueError("a note needs some text")
    sheet = _sheet(name)
    if not sheet.foldered:
        raise ValueError(
            f"'{name}' is a single file, so it has nowhere to keep a history. "
            f"Move it to styles/{name}/style.yaml first."
        )
    stylelog.append(sheet.home, stylelog.note_event(text))
    return {"ok": True, "history": stylelog.read(sheet.home)}


def start_run(config_name: str, overrides: dict | None, resume: str | None,
              style_picks: dict | None = None) -> str:
    cmd = [sys.executable, "-u", str(ROOT / "run.py")]

    if resume:
        out = runs_dir() / resume
        if not out.is_dir():
            raise FileNotFoundError(resume)
        run_id = resume
        cmd += ["--resume", resume]
        log_mode = "a"
    else:
        cfg_path = CONFIGS / f"{config_name}.yaml"
        if not cfg_path.exists():
            raise FileNotFoundError(config_name)
        run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{config_name}"
        out = runs_dir() / run_id
        out.mkdir(parents=True, exist_ok=True)

        # Unsaved edits from the UI are materialised into the run directory so
        # a run always records the config it actually used.
        effective = cfg_path
        if overrides or style_picks:
            merged = yaml.safe_load(cfg_path.read_text()) or {}
            for path, value in (overrides or {}).items():
                schema.set_path(merged, path, value)
            if style_picks:
                # A one-off narrowing of the style vocabulary, recorded in the
                # run's own config so the result stays reproducible.
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
        from pipeline.comfy import Client

        c = Client()
        services["comfyui"] = {"url": c.host, "up": c.alive()}
    except Exception as e:  # pragma: no cover
        services["comfyui"] = {"up": False, "error": str(e)}
    try:
        from pipeline.llm import Ollama

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
        # Stated plainly so the UI can explain why there is no GPU-core slider.
        "compute_note": (
            "Metal exposes no way to partition the GPU between processes — "
            "there is no equivalent of CUDA_VISIBLE_DEVICES or MIG. GPU load is "
            "controlled by how much work you send it (steps, resolution, batch, "
            "model size) and by the memory ceiling, not by a core count."
        ),
    }


# ------------------------------------------------------------------- handler


class Handler(BaseHTTPRequestHandler):
    server_version = "SpritePipeline"

    def log_message(self, fmt, *args):  # quieter console
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode(), "application/json")

    def _error(self, code: int, msg: str) -> None:
        self._json({"error": msg}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # -- GET

    def do_GET(self) -> None:
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            self._get(u.path, q)
        except FileNotFoundError as e:
            self._error(404, str(e))
        except (PermissionError, files_mod.PathDenied) as e:
            self._error(403, str(e))
        except Exception as e:  # pragma: no cover
            self._error(500, f"{type(e).__name__}: {e}")

    def _get(self, path: str, q: dict) -> None:
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])

        if path == "/api/schema":
            return self._json(schema.describe(ROOT, q.get("module", [""])[0] or None))
        if path == "/api/system":
            return self._json(system_info())
        if path == "/api/global":
            return self._json({"config": global_cfg()})
        if path == "/api/configs":
            CONFIGS.mkdir(exist_ok=True)
            found = []
            for f in sorted(CONFIGS.glob("*.yaml")):
                if f.stem == settings.GLOBAL_NAME:
                    continue
                try:
                    cfg = yaml.safe_load(f.read_text()) or {}
                except yaml.YAMLError:
                    cfg = {}
                found.append({"name": f.stem, "module": cfg.get("module", "animation")})
            return self._json({"configs": [c["name"] for c in found], "detail": found})
        if path == "/api/config":
            name = q.get("name", [""])[0]
            p = CONFIGS / f"{name}.yaml"
            if not p.exists():
                raise FileNotFoundError(f"no config '{name}'")
            own = yaml.safe_load(p.read_text()) or {}
            return self._json(
                {
                    "name": name,
                    "module": own.get("module", "animation"),
                    "raw": p.read_text(),
                    "config": own,                       # what this file pins
                    "effective": settings.effective(ROOT, styles.layer(ROOT, own)[0]),
                    "style_record": styles.layer(ROOT, own)[1],
                    "overrides": sorted(settings.overridden_paths(own)),
                }
            )
        if path == "/api/runs":
            return self._json({"runs": list_runs()})
        if path == "/api/run":
            return self._json(run_detail(q.get("id", [""])[0]))
        if path == "/api/poses":
            return self._json(self._poses(q.get("run", [""])[0]))
        if path == "/api/styles":
            found = styles.discover(ROOT)
            return self._json({
                "styles": [st.summary(ROOT) for st in found.values()],
            })
        if path == "/api/style/preview":
            name = q.get("config", [""])[0]
            cfg_file = CONFIGS / f"{name}.yaml"
            own = yaml.safe_load(cfg_file.read_text()) if cfg_file.exists() else {}
            extra = q.get("with", [])
            merged = dict(own or {})
            if extra:
                merged["styles"] = list(dict.fromkeys(
                    list(merged.get("styles") or []) + extra))
            return self._json(styles.preview(ROOT, merged))
        if path == "/api/style/detail":
            return self._json(style_detail(q.get("name", [""])[0]))
        if path == "/api/style/training":
            from pipeline import training

            return self._json(training.preview(_sheet(q.get("name", [""])[0]).home))

        if path == "/api/annotation":
            from pipeline import annotate

            image = files_mod.safe_path(q.get("image", [""])[0], allowed_roots())
            found = annotate.load(image)
            if not found:
                return self._json({"image": str(image), "points": {},
                                   "rig": q.get("rig", ["humanoid"])[0],
                                   "placed": 0, "missing": [], "exists": False})
            return self._json({**annotate.describe(found), "exists": True})

        if path == "/api/autorig":
            from pipeline import autorig

            image = files_mod.safe_path(q.get("image", [""])[0], allowed_roots())
            if not image.is_file():
                raise FileNotFoundError(q.get("image", [""])[0])
            fit = autorig.propose(image, q.get("rig", ["humanoid"])[0],
                                  int(q.get("tolerance", ["18"])[0]))
            return self._json(fit.as_dict())

        if path == "/api/rigpose":
            from pipeline import rigs as rig_lib

            rig = rig_lib.get(q.get("rig", [""])[0] or None)
            symmetric = q.get("symmetric", ["0"])[0] == "1"
            return self._json({
                "rig": rig.name, "label": rig.label,
                "joints": list(rig.joints),
                "tree": {k: list(v) for k, v in rig.tree.items()},
                "root": rig.root,
                "limbs": [list(p) for p in rig.limb_pairs],
                "bones": [[a, b, t] for a, b, t in rig.bones],
                "neutral": {k: list(v) for k, v in rig.neutral.items()},
                "face_joints": list(rig.face_joints),
                "colors": [list(rig.color(i)) for i in range(len(rig.bones))],
                "pose": rig_lib.tpose(rig, symmetric=symmetric),
            })
        if path == "/api/browse":
            target = q.get("path", [""])[0] or str(input_dir())
            d = files_mod.safe_path(target, allowed_roots())
            return self._json(
                files_mod.browse(d, images_only=q.get("images", ["0"])[0] == "1")
            )
        if path == "/api/file":
            return self._file(q.get("path", [""])[0])

        raise FileNotFoundError(path)

    def _poses(self, run_id: str) -> dict:
        """Body-space pose data for the rig editor.

        Served from a run's pose stage when given one, otherwise from the
        authored library, so the editor can open either.
        """
        if run_id:
            p = runs_dir() / run_id
            for d in sorted(p.glob("*_pose")):
                f = d / "pose.json"
                if f.exists():
                    data = json.loads(f.read_text())
                    data["source_file"] = str(f)
                    # The editor needs the topology, not just the name, to draw
                    # and drag a non-humanoid skeleton correctly.
                    from pipeline import rigs as rig_lib

                    rig = rig_lib.get(data.get("rig"))
                    data["rig_def"] = {
                        "name": rig.name, "joints": list(rig.joints),
                        "tree": {k: list(v) for k, v in rig.tree.items()},
                        "root": rig.root,
                        "limbs": [list(p) for p in rig.limb_pairs],
                        "bones": [[a, b, t] for a, b, t in rig.bones],
                        "neutral": {k: list(v) for k, v in rig.neutral.items()},
                    }
                    return data
            raise FileNotFoundError(f"run {run_id} has no pose stage output")

        library = {}
        for f in sorted((ROOT / "poses").glob("*.json")):
            library[f.stem] = json.loads(f.read_text())
        return {"library": library}

    # -- POST

    def do_POST(self) -> None:
        u = urllib.parse.urlparse(self.path)
        try:
            if u.path == "/api/upload":
                return self._upload()

            body = self._body()
            if u.path == "/api/run":
                run_id = start_run(
                    body.get("config", ""), body.get("overrides"), body.get("resume"),
                    body.get("style_picks"),
                )
                return self._json({"run_id": run_id})
            if u.path == "/api/stop":
                rid = body.get("run_id", "")
                with _LOCK:
                    proc = _ACTIVE.get(rid)
                if proc and proc.poll() is None:
                    proc.terminate()
                    return self._json({"stopped": rid})
                return self._error(404, "no such active run")
            if u.path == "/api/download/plan":
                return self._json(self._download(body, dry_run=True))
            if u.path == "/api/download":
                return self._json(self._download(body, dry_run=False))
            if u.path == "/api/poses":
                return self._json(self._save_poses(body))
            if u.path == "/api/annotation":
                return self._json(self._save_annotation(body))
            if u.path == "/api/style/note":
                return self._json(add_style_note(
                    body.get("name", ""), body.get("text", "")))
            raise FileNotFoundError(u.path)
        except FileNotFoundError as e:
            self._error(404, str(e))
        except (PermissionError, files_mod.PathDenied) as e:
            self._error(403, str(e))
        except Exception as e:
            self._error(500, f"{type(e).__name__}: {e}")

    def _upload(self) -> None:
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._error(400, "expected multipart/form-data")

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._error(400, "empty upload")
        if length > 256 * 1024 * 1024:
            return self._error(413, "upload too large")

        parts = files_mod.parse_multipart(self.rfile.read(length), ctype)
        target = input_dir()
        saved = []
        for _, filename, data in parts:
            if not filename or not data:
                continue
            dst = files_mod.unique_name(target, files_mod.safe_filename(filename))
            dst.write_bytes(data)
            saved.append({"name": dst.name, "path": str(dst)})

        if not saved:
            return self._error(400, "no files in the upload")
        return self._json({"saved": saved, "dir": str(target)})

    def _download(self, body: dict, dry_run: bool) -> dict:
        run_id = body.get("run_id", "")
        stage = body.get("stage")
        run = runs_dir() / run_id
        if not run.is_dir():
            raise FileNotFoundError(run_id)

        sources: list[Path] = []
        for d in sorted(run.iterdir()):
            if not d.is_dir() or not re.match(r"^\d\d_", d.name):
                continue
            if stage and d.name.split("_", 1)[1] != stage:
                continue
            sources += sorted(d.glob("*.png"))
        if not sources:
            raise FileNotFoundError("nothing to download for that selection")

        target_raw = body.get("target") or str(download_dir() / run_id)
        target = files_mod.safe_path(target_raw, allowed_roots())

        if dry_run:
            return files_mod.plan_copy(sources, target)
        return files_mod.copy_files(sources, target, bool(body.get("overwrite")))

    def _save_poses(self, body: dict) -> dict:
        """Write edited skeletons back and re-render their control images.

        The rig editor changes body-space data, but the frames stage consumes
        rendered PNGs — so saving has to redraw them, or the edit would be
        recorded and then ignored.
        """
        run_id = body.get("run_id", "")
        entries = body.get("entries")
        if not run_id or not isinstance(entries, list):
            raise ValueError("run_id and entries are required")

        run = runs_dir() / run_id
        pose_dirs = sorted(run.glob("*_pose"))
        if not pose_dirs:
            raise FileNotFoundError(f"run {run_id} has no pose stage output")
        pose_dir = pose_dirs[0]

        from pipeline.bodyspace import project
        from pipeline.depthmap import render_depth
        from pipeline.openpose import render

        cfg = yaml.safe_load((run / "config.yaml").read_text()) or {}
        pose_cfg = cfg.get("pose") or {}
        size = pose_cfg.get("size") or 1024
        ds = pose_cfg.get("depth_scale", 1.0) or 1.0
        ls = pose_cfg.get("lateral_scale", 1.0) or 1.0

        existing = json.loads((pose_dir / "pose.json").read_text())
        existing["entries"] = entries
        (pose_dir / "pose.json").write_text(json.dumps(existing, indent=1))

        # The rig has to come along. Without it the renderer falls back to the
        # humanoid's 18-joint OpenPose layout and happily draws a spider's
        # eight legs as a mangled person — a control image that is wrong in a
        # way nothing downstream can detect, since it is still a valid PNG.
        from pipeline import rigs as rig_lib

        rig = rig_lib.get(cfg.get("rig") if cfg.get("rig") != "auto" else rig_lib.DEFAULT)
        thickness = pose_cfg.get("thickness")

        for i, entry in enumerate(entries):
            keypoints = project(
                entry["pose"], entry["yaw"], depth_scale=ds, lateral_scale=ls
            )
            render(keypoints, size, size, thickness=thickness, rig=rig).save(
                pose_dir / f"skeleton_{i:03d}.png")

        depth_dirs = sorted(run.glob("*_depth"))
        if depth_dirs:
            dcfg = cfg.get("depth") or {}
            for i, entry in enumerate(entries):
                render_depth(
                    entry["pose"], entry["yaw"], size, size,
                    near=dcfg.get("near", 255), far=dcfg.get("far", 60),
                    blur=dcfg.get("blur", 6.0),
                    depth_scale=ds, lateral_scale=ls,
                ).save(depth_dirs[0] / f"depth_{i:03d}.png")

        # The manifest must match the new frame count, or a resume would hand
        # stale skeleton paths to the frames stage. A run written before the
        # typed manifest existed cannot be repaired, so say so rather than
        # leaving a resume to fail later with a confusing error.
        skeletons = [pose_dir / f"skeleton_{i:03d}.png" for i in range(len(entries))]
        depthmaps = (
            [depth_dirs[0] / f"depth_{i:03d}.png" for i in range(len(entries))]
            if depth_dirs else None
        )
        manifest_state = "updated"
        try:
            arts, completed = artifacts_io.load(run)
            arts["skeletons"] = skeletons
            arts["pose_frames"] = entries
            if depthmaps:
                arts["depthmaps"] = depthmaps
            artifacts_io.save(run, arts, completed)
        except (FileNotFoundError, ValueError):
            # No usable manifest: write one from what we just rendered, so the
            # run becomes resumable rather than staying stuck.
            arts = {"skeletons": skeletons, "pose_frames": entries}
            if depthmaps:
                arts["depthmaps"] = depthmaps
            completed = ["pose"] + (["depth"] if depthmaps else [])
            artifacts_io.save(run, arts, completed)
            manifest_state = "rebuilt"

        return {"saved": len(entries), "dir": str(pose_dir), "manifest": manifest_state}

    def _save_annotation(self, body: dict) -> dict:
        """Store an image annotation beside its image, and report what it implies."""
        from pipeline import annotate

        image = files_mod.safe_path(body.get("image", ""), allowed_roots())
        if not image.is_file():
            raise FileNotFoundError(body.get("image", ""))

        points = {
            k: [float(v[0]), float(v[1])]
            for k, v in (body.get("points") or {}).items()
            if isinstance(v, (list, tuple)) and len(v) == 2
        }
        ann = annotate.Annotation(
            image=image, rig=body.get("rig", "humanoid"),
            points=points, note=body.get("note", ""),
        )
        annotate.save(ann)
        return {**annotate.describe(ann), "saved": True}

    # -- PUT

    def do_PUT(self) -> None:
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/api/global":
                return self._json(self._save_global(self._body()))
            if u.path != "/api/config":
                raise FileNotFoundError(u.path)
            return self._json(self._save_config(q.get("name", [""])[0], self._body()))
        except ValueError as e:
            self._error(400, str(e))
        except Exception as e:
            self._error(500, f"{type(e).__name__}: {e}")

    def _save_config(self, name: str, body: dict) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", name or ""):
            raise ValueError("invalid config name")
        CONFIGS.mkdir(exist_ok=True)
        target = CONFIGS / f"{name}.yaml"

        if "raw" in body:
            parsed = yaml.safe_load(body["raw"])  # reject invalid YAML first
            problem = validate_order(parsed)
            if problem and not body.get("force"):
                raise ValueError(problem)
            target.write_text(body["raw"])
            return {"saved": name}

        incoming = body.get("config", {}) or {}
        problem = validate_order(incoming)
        if problem and not body.get("force"):
            # Refuse rather than persist an order that cannot run. The runner
            # would catch it later, but only after the user had already lost
            # the working config.
            raise ValueError(problem)

        if target.exists():
            doc = load_roundtrip(target)
            # Explicit resets remove the key so the value falls back to global.
            for dotted in body.get("unset", []) or []:
                settings.unset(doc, dotted)
            changed = _apply_changes(doc, incoming)
            dump_roundtrip(doc, target)
            return {"saved": name, "changed": changed}

        target.write_text(yaml.safe_dump(incoming, sort_keys=False))
        return {"saved": name, "changed": -1}

    def _save_global(self, body: dict) -> dict:
        path = settings.global_path(ROOT)
        incoming = body.get("config", {}) or {}
        if path.exists():
            doc = load_roundtrip(path)
            changed = _apply_changes(doc, incoming)
            dump_roundtrip(doc, path)
        else:
            path.write_text(yaml.safe_dump(incoming, sort_keys=False))
            changed = -1
        return {"saved": settings.GLOBAL_NAME, "changed": changed}

    # -- static / files

    def _static(self, rel: str) -> None:
        p = (STATIC / rel).resolve()
        if not str(p).startswith(str(STATIC)) or not p.is_file():
            raise FileNotFoundError(rel)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if p.suffix == ".js":
            ctype = "text/javascript"
        self._send(200, p.read_bytes(), ctype)

    def _file(self, rel: str) -> None:
        p = files_mod.safe_path(rel, allowed_roots())
        if not p.is_file():
            raise FileNotFoundError(rel)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        self._send(200, p.read_bytes(), ctype)


def main() -> int:
    port = int(os.environ.get("PORT", 8000))
    STATIC.mkdir(exist_ok=True)
    runs_dir()
    input_dir()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"sprite pipeline UI -> http://127.0.0.1:{port}")
    print(f"  stages: {', '.join(sorted(available()))}")
    print(f"  inputs: {input_dir()}")
    print(f"  runs:   {runs_dir()}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
