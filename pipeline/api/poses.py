"""Pose guides and reference annotation: the geometry a run is conditioned on.

These were methods on the request handler that never touched `self` - pure
logic wearing a method's clothes, and untestable only because of where they
sat.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import annotate, autorig, bodyspace
from .. import rigs as rig_lib
from ..shared.errors import Invalid, NotFound
from .context import ROOT, load_roundtrip, runs_dir
from .routing import BaseRouter, get, post
from .context import allowed_roots
from .. import files as files_mod
from .. import artifacts as artifacts_io
import yaml


def poses(run_id: str) -> dict:
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


def save_poses(body: dict) -> dict:
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


def save_annotation(body: dict) -> dict:
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


class Poses(BaseRouter):
    prefix = "/api"

    @get("/poses", "the pose guides a run used, or the library")
    def list(self, req):
        return poses(req.query("run"))

    @post("/poses", "save edited pose guides")
    def save(self, req):
        return save_poses(req.body)

    @get("/annotation", "a saved annotation for one reference image")
    def annotation(self, req):
        return annotate.load(ROOT, req.required("image"),
                             req.query("rig", "humanoid"))

    @post("/annotation", "save an annotation")
    def save_annotation(self, req):
        return save_annotation(req.body)

    @get("/autorig", "fit a rig to a reference image")
    def autorig(self, req):
        return autorig.fit(Path(req.required("image")),
                           req.query("rig", "humanoid"))

    @get("/rigpose", "a rig's whole topology, not only its pose")
    def rigpose(self, req):
        # The editor draws bones, colours them by chain and knows which joints
        # are a face, so it needs the topology rather than a list of points.
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
