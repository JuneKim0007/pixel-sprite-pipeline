"""What PoseStage.run decides, driven through the source that needs nothing."""

from __future__ import annotations

import json

import pytest

from pipeline.generation.stage import get
from pipeline.geometry import rigs
from pipeline.shared.errors import Invalid


def run(ctx):
    return get("pose")().run(ctx, {})


def test_a_plain_config_makes_one_entry_at_the_configured_view(stage_ctx):
    out = run(stage_ctx(pose={"source": "tpose", "view": "side"}))
    entries = out["pose_frames"]

    assert len(entries) == 1
    assert entries[0]["yaw"] == 90.0
    assert entries[0]["spec"] == 0
    assert len(out["skeletons"]) == 1 and out["skeletons"][0].exists()


def test_a_set_makes_one_group_of_entries_per_spec(stage_ctx):
    out = run(stage_ctx(pose={"source": "tpose", "set": [{"view": "front"}, {"view": "rear"}]}))
    entries = out["pose_frames"]

    assert [e["yaw"] for e in entries] == [0.0, 180.0]
    assert [e["spec"] for e in entries] == [0, 1]


def test_a_spec_inherits_the_block_and_overrides_it(stage_ctx):
    out = run(stage_ctx(pose={"source": "tpose", "view": "front", "symmetric": True, "set": [{"view": "rear"}]}))
    assert out["pose_frames"][0]["yaw"] == 180.0, "the spec's view wins"


def test_a_spec_that_is_not_a_mapping_names_its_index(stage_ctx):
    with pytest.raises(Invalid, match=r"pose\.set\[1\] must be a mapping"):
        run(stage_ctx(pose={"source": "tpose", "set": [{"view": "front"}, "front"]}))


def test_a_frame_index_past_the_end_says_how_many_there_are(stage_ctx):
    with pytest.raises(Invalid, match="frame=3 but that pose has 1 frame"):
        run(stage_ctx(pose={"source": "tpose", "set": [{"view": "front", "frame": 3}]}))


def test_a_frame_index_picks_exactly_one(stage_ctx):
    out = run(stage_ctx(pose={"source": "tpose", "set": [{"view": "front", "frame": 0}]}))
    assert len(out["pose_frames"]) == 1


def test_a_rig_without_joints_writes_a_manifest_and_no_skeletons(stage_ctx):
    ctx = stage_ctx(rig=rigs.NONE, pose={"source": "tpose", "view": "side"})
    out = run(ctx)

    assert out["skeletons"] == []
    assert len(out["pose_frames"]) == 1
    written = json.loads((ctx.outdir / "00_pose" / "pose.json").read_text())
    assert written["mode"] == "rig_free"
    assert written["rig"] == rigs.NONE.name


def test_a_rigged_run_writes_one_skeleton_per_entry(stage_ctx):
    out = run(stage_ctx(pose={"source": "tpose", "set": [{"view": "front"}, {"view": "side"},
                            {"view": "rear"}]}))
    assert len(out["skeletons"]) == 3
    assert len(out["pose_frames"]) == 3
    assert all(p.exists() for p in out["skeletons"])


def test_the_manifest_records_the_entries_it_rendered(stage_ctx):
    ctx = stage_ctx(pose={"source": "tpose", "set": [{"view": "front"}, {"view": "rear"}]})
    run(ctx)
    written = json.loads((ctx.outdir / "00_pose" / "pose.json").read_text())
    assert [e["yaw"] for e in written["entries"]] == [0.0, 180.0]


def test_an_unknown_source_is_clamped_before_the_stage_sees_it(stage_ctx, caplog):
    """`_resolve` raises NotFound for an unknown source and cannot be reached
    that way: pose.source is a declared select, so the schema clamps first."""
    ctx = stage_ctx(pose={"source": "telepathy"})
    assert ctx.settings("pose")["source"] == "library"


def _many(n):
    """A _resolve that yields n poses, so run()'s frame handling is reachable."""
    def resolve(self, ctx, cfg, wanted):
        return [{"joint": i} for i in range(n)]
    return resolve


def test_a_frame_index_picks_that_one_out_of_several(stage_ctx, monkeypatch):
    stage = get("pose")()
    monkeypatch.setattr(type(stage), "_resolve", _many(4))
    out = stage.run(stage_ctx(pose={"source": "tpose", "set": [{"view": "front", "frame": 2}]}), {})

    assert len(out["pose_frames"]) == 1
    assert out["pose_frames"][0]["pose"] == {"joint": 2}


def test_without_a_frame_index_every_pose_becomes_an_entry(stage_ctx, monkeypatch):
    stage = get("pose")()
    monkeypatch.setattr(type(stage), "_resolve", _many(3))
    out = stage.run(stage_ctx(pose={"source": "tpose", "set": [{"view": "front"}]}), {})

    assert [e["pose"] for e in out["pose_frames"]] == [
        {"joint": 0}, {"joint": 1}, {"joint": 2}]


def _annotated(tmp_path, png, points):
    """A reference image with a rig sidecar beside it, as the Annotate tab writes."""
    from pipeline.geometry import annotate as ann
    from pipeline.refs import references as refs_mod

    image = tmp_path / "front_px.png"
    png(image, (64, 64))
    ann.save(ann.Annotation(image=image, rig="humanoid", points=points, note=""))
    return refs_mod.Library(identity=[refs_mod.Reference(
        path=image, label="front", yaw=0.0, role="identity")])


ARMS_DOWN = {
    "nose": [0.508, 0.21], "l_eye": [0.488, 0.196], "r_eye": [0.528, 0.196],
    "neck": [0.505, 0.238],
    "l_shoulder": [0.457, 0.272], "r_shoulder": [0.566, 0.272],
    "l_elbow": [0.432, 0.382], "r_elbow": [0.59, 0.382],
    "l_wrist": [0.402, 0.502], "r_wrist": [0.603, 0.496],
    "l_hip": [0.478, 0.543], "r_hip": [0.538, 0.543],
    "l_knee": [0.472, 0.675], "r_knee": [0.562, 0.675],
    "l_ankle": [0.477, 0.845], "r_ankle": [0.581, 0.845],
}


def test_an_annotated_pose_survives_a_set(stage_ctx, tmp_path, png):
    """`set` reached _from_spec, which wrapped the Annotation as if it were a
    pose and crashed in frame_scale. Every sweep config carries a `set`, so the
    source was unreachable from one."""
    ctx = stage_ctx(pose={"source": "annotation", "set": [{"view": "front"}]})
    ctx.resources["references"] = _annotated(tmp_path, png, ARMS_DOWN)

    out = run(ctx)
    assert len(out["skeletons"]) == 1 and out["skeletons"][0].exists()
    assert out["pose_frames"][0]["from_annotation"].endswith("front_px.png")


def test_the_annotation_drives_the_guide_not_the_rig(stage_ctx, tmp_path, png):
    """The point of the source: char1's reference has the arms tucked in, and a
    tpose guide asks for them out. Same rig, two different control images."""
    marked = stage_ctx(pose={"source": "annotation", "set": [{"view": "front"}]})
    marked.resources["references"] = _annotated(tmp_path, png, ARMS_DOWN)
    synthesised = stage_ctx(pose={"source": "tpose", "set": [{"view": "front"}]})

    a = run(marked)["skeletons"][0].read_bytes()
    b = run(synthesised)["skeletons"][0].read_bytes()
    assert a != b, "the annotation made no difference to the control image"
