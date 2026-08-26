

from __future__ import annotations

import json
from pathlib import Path

from ..geometry import annotate, autorig
from ..geometry import rigs as rig_lib
from ..generation.stage import Context
from ..looks import styles
from ..orchestration import artifacts as artifacts_io
from ..shared import files as files_mod
from ..shared import settings
from ..shared.errors import Invalid, NotFound
from ..stages import depth as depth_stage
from ..stages import pose as pose_stage
from .context import ROOT, allowed_roots, runs_dir
from .contracts import Shape
from .routing import BaseRouter, get, post
import yaml


def pose_library() -> dict:
    from ..looks import poses as pose_lib

    return {"library": {k: json.loads(p.read_text())
                        for k, p in pose_lib.discover(ROOT).items()}}


def run_poses(run_id: str) -> dict:
    """The pose guides one run produced, with the rig needed to draw them."""
    for d in sorted((runs_dir() / run_id).glob("*_pose")):
        f = d / "pose.json"
        if not f.exists():
            continue
        data = json.loads(f.read_text())
        data["source_file"] = str(f)

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
    raise NotFound("pose stage output", run_id)


def _run_context(run: Path, pose_json: dict) -> Context:
    raw = yaml.safe_load((run / "config.yaml").read_text()) or {}
    styled, _record = styles.layer(ROOT, raw, picks=raw.get("style_picks"))
    cfg = settings.effective(ROOT, styled)
    recorded = pose_json.get("rig")
    if recorded:
        cfg = {**cfg, "rig": recorded}
    return Context(root=ROOT, outdir=run, config=cfg, run_id=run.name)


def save_poses(body: dict) -> dict:
    run_id = body.get("run_id", "")
    entries = body.get("entries")
    if not run_id:
        raise Invalid("'run_id' is required", field="run_id")
    if not isinstance(entries, list):
        raise Invalid(f"'entries' must be a list of poses, not "
                      f"{type(entries).__name__}", field="entries")

    run = runs_dir() / run_id
    pose_dirs = sorted(run.glob("*_pose"))
    if not pose_dirs:
        raise NotFound("pose stage output", run_id)
    pose_dir = pose_dirs[0]

    existing = json.loads((pose_dir / "pose.json").read_text())
    existing["entries"] = entries
    (pose_dir / "pose.json").write_text(json.dumps(existing, indent=1))

    ctx = _run_context(run, existing)
    skeletons = pose_stage.render_entries(ctx, entries, pose_dir)

    depth_dirs = sorted(run.glob("*_depth"))
    depthmaps = (
        depth_stage.render_entries(ctx, entries, depth_dirs[0])
        if depth_dirs else None
    )

    # Manifest must match the new frame count, or a resume hands stale skeleton paths to the frames stage.
    manifest_state = "updated"
    try:
        arts, completed = artifacts_io.load(run)
        arts["skeletons"] = skeletons
        arts["pose_frames"] = entries
        if depthmaps:
            arts["depthmaps"] = depthmaps
        artifacts_io.save(run, arts, completed)
    except (NotFound, Invalid, ValueError, OSError):
        # Both are ours to catch here too, or the exact "no usable manifest" case this block exists for reaches the caller as a raw 400/500 instead of getting repaired.
        arts = {"skeletons": skeletons, "pose_frames": entries}
        if depthmaps:
            arts["depthmaps"] = depthmaps
        completed = ["pose"] + (["depth"] if depthmaps else [])
        artifacts_io.save(run, arts, completed)
        manifest_state = "rebuilt"

    return {"saved": len(entries), "dir": str(pose_dir), "manifest": manifest_state}


def _save_annotation(body: dict) -> dict:
    """Store an image annotation beside its image, and report what it implies."""
    from pipeline.geometry import annotate

    image = files_mod.safe_path(body.get("image", ""), allowed_roots())
    if not image.is_file():
        raise NotFound("image", body.get("image", ""))

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


class Poses(BaseRouter):
    prefix = "/api"

    @get("/poses", "every pose guide in the library",
         returns=Shape(library=dict))
    def index(self, req):
        return pose_library()

    @get("/run/poses", "the pose guides one run produced",
         returns=Shape(entries=list, rig=str, rig_def=dict, source_file=str))
    def for_run(self, req):
        return run_poses(req.required("run"))

    @post("/poses", "save edited pose guides",
          returns=Shape(saved=int, dir=str, manifest=str))
    def save(self, req):
        return save_poses(req.body)

    @get("/annotation", "a saved annotation for one reference image",
         returns=Shape(exists=bool, image=str, rig=str, points=dict))
    def annotation(self, req):
        image = files_mod.safe_path(req.required("image"), allowed_roots())
        rig = req.query("rig", "humanoid")
        found = annotate.load(image)
        if found is None:
            return {"exists": False, "image": str(image), "rig": rig,
                    "points": {}}
        return {**annotate.describe(found), "exists": True}

    @post("/annotation", "save an annotation",
          returns=Shape(saved=bool, image=str, rig=str, points=dict))
    def save_annotation(self, req):
        return _save_annotation(req.body)

    @get("/autorig", "fit a rig to a reference image",
         returns=Shape(points=dict, proportions=dict, confidence=(int, float),
                       notes=list))
    def autorig(self, req):
        image = files_mod.safe_path(req.required("image"), allowed_roots())
        if not image.is_file():
            raise NotFound("image", req.query("image"))
        return autorig.propose(image, req.query("rig", "humanoid")).as_dict()

    @get("/rigpose", "a rig's whole topology, not only its pose",
         returns=Shape(rig=str, label=str, joints=list, tree=dict, root=str,
                       limbs=list, bones=list, neutral=dict, face_joints=list,
                       colors=list, pose=dict))
    def rigpose(self, req):
        rig = rig_lib.get(req.query("rig") or None)
        return {
            "rig": rig.name, "label": rig.label,
            "joints": list(rig.joints),
            "tree": {k: list(v) for k, v in rig.tree.items()},
            "root": rig.root,
            "limbs": [list(p) for p in rig.limb_pairs],
            "bones": [[a, b, t] for a, b, t in rig.bones],
            "neutral": {k: list(v) for k, v in rig.neutral.items()},
            "face_joints": list(rig.face_joints),
            "colors": [list(rig.color(i)) for i in range(len(rig.bones))],
            "pose": rig_lib.tpose(rig, symmetric=req.flag("symmetric")),
        }
