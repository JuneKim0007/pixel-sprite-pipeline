"""What PoseStage.run decides, driven through the source that needs nothing.

`source: tpose` reaches the rig and nothing else — no library, no annotations,
no LLM — so the part of run() worth pinning is reachable: how `pose.set`
becomes entries, how a spec picks one frame out of several, and what a rig with
no joints produces instead of skeletons.
"""

from __future__ import annotations

import json

import pytest

from pipeline.generation.stage import Context, get
from pipeline.geometry import rigs
from pipeline.shared.errors import Invalid
from pipeline.refs import references as refs_mod


@pytest.fixture
def pose_ctx(tmp_path):
    def build(rig=rigs.HUMANOID, **pose):
        cfg = {"cooling": {"enabled": False},
               "pose": {"source": "tpose", **pose}}
        outdir = tmp_path / "run"
        outdir.mkdir(parents=True, exist_ok=True)
        return Context(root=tmp_path, outdir=outdir, config=cfg, run_id="r",
                       artifacts={},
                       resources={"rig": rig, "references": refs_mod.Library(),
                                  "rig_record": {"source": "test"}})
    return build


def run(ctx):
    return get("pose")().run(ctx, {})


def test_a_plain_config_makes_one_entry_at_the_configured_view(pose_ctx):
    out = run(pose_ctx(view="side"))
    entries = out["pose_frames"]

    assert len(entries) == 1
    assert entries[0]["yaw"] == 90.0
    assert entries[0]["spec"] == 0
    assert len(out["skeletons"]) == 1 and out["skeletons"][0].exists()


def test_a_set_makes_one_group_of_entries_per_spec(pose_ctx):
    out = run(pose_ctx(set=[{"view": "front"}, {"view": "rear"}]))
    entries = out["pose_frames"]

    assert [e["yaw"] for e in entries] == [0.0, 180.0]
    assert [e["spec"] for e in entries] == [0, 1]


def test_a_spec_inherits_the_block_and_overrides_it(pose_ctx):
    out = run(pose_ctx(view="front", symmetric=True,
                       set=[{"view": "rear"}]))
    assert out["pose_frames"][0]["yaw"] == 180.0, "the spec's view wins"


def test_a_spec_that_is_not_a_mapping_names_its_index(pose_ctx):
    with pytest.raises(Invalid, match=r"pose\.set\[1\] must be a mapping"):
        run(pose_ctx(set=[{"view": "front"}, "front"]))


def test_a_frame_index_past_the_end_says_how_many_there_are(pose_ctx):
    with pytest.raises(Invalid, match="frame=3 but that pose has 1 frame"):
        run(pose_ctx(set=[{"view": "front", "frame": 3}]))


def test_a_frame_index_picks_exactly_one(pose_ctx):
    out = run(pose_ctx(set=[{"view": "front", "frame": 0}]))
    assert len(out["pose_frames"]) == 1


def test_a_rig_without_joints_writes_a_manifest_and_no_skeletons(pose_ctx):
    ctx = pose_ctx(rig=rigs.NONE, view="side")
    out = run(ctx)

    assert out["skeletons"] == []
    assert len(out["pose_frames"]) == 1
    written = json.loads((ctx.outdir / "00_pose" / "pose.json").read_text())
    assert written["mode"] == "rig_free"
    assert written["rig"] == rigs.NONE.name


def test_a_rigged_run_writes_one_skeleton_per_entry(pose_ctx):
    out = run(pose_ctx(set=[{"view": "front"}, {"view": "side"},
                            {"view": "rear"}]))
    assert len(out["skeletons"]) == 3
    assert len(out["pose_frames"]) == 3
    assert all(p.exists() for p in out["skeletons"])


def test_the_manifest_records_the_entries_it_rendered(pose_ctx):
    ctx = pose_ctx(set=[{"view": "front"}, {"view": "rear"}])
    run(ctx)
    written = json.loads((ctx.outdir / "00_pose" / "pose.json").read_text())
    assert [e["yaw"] for e in written["entries"]] == [0.0, 180.0]


def test_an_unknown_source_is_clamped_before_the_stage_sees_it(pose_ctx, caplog):
    """`_resolve` raises NotFound for an unknown source and cannot be reached
    that way: pose.source is a declared select, so the schema clamps first."""
    ctx = pose_ctx(source="telepathy")
    assert ctx.settings("pose")["source"] == "library"


def _many(n):
    """A _resolve that yields n poses, so run()'s frame handling is reachable.

    `tpose` always returns exactly one, so the `frame` index and the `frames`
    count cannot be exercised through a real source here.
    """
    def resolve(self, ctx, cfg, wanted):
        return [{"joint": i} for i in range(n)]
    return resolve


def test_a_frame_index_picks_that_one_out_of_several(pose_ctx, monkeypatch):
    stage = get("pose")()
    monkeypatch.setattr(type(stage), "_resolve", _many(4))
    out = stage.run(pose_ctx(set=[{"view": "front", "frame": 2}]), {})

    assert len(out["pose_frames"]) == 1
    assert out["pose_frames"][0]["pose"] == {"joint": 2}


def test_without_a_frame_index_every_pose_becomes_an_entry(pose_ctx, monkeypatch):
    stage = get("pose")()
    monkeypatch.setattr(type(stage), "_resolve", _many(3))
    out = stage.run(pose_ctx(set=[{"view": "front"}]), {})

    assert [e["pose"] for e in out["pose_frames"]] == [
        {"joint": 0}, {"joint": 1}, {"joint": 2}]
