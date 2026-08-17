from __future__ import annotations

import copy
import json
import shutil

import pytest
import yaml

from pipeline.api.context import runs_dir
from pipeline.api.poses import save_poses
from pipeline.generation.stage import Context
from pipeline.geometry import rigs
from pipeline.looks import styles
from pipeline.shared import settings
from pipeline.stages import pose as pose_stage


def _run(tag: str, pose_cfg: dict):
    run = runs_dir() / f"_test_{tag}"
    shutil.rmtree(run, ignore_errors=True)
    (run / "00_pose").mkdir(parents=True)
    (run / "config.yaml").write_text(yaml.safe_dump(
        {"name": tag, "rig": "humanoid", "pipeline": {"stages": ["pose"]},
         "pose": pose_cfg}, sort_keys=False))

    rig = rigs.get("humanoid")
    entries = [{"pose": {k: list(v) for k, v in rigs.tpose(rig).items()},
                "yaw": 30.0, "spec": 0}]
    (run / "00_pose" / "pose.json").write_text(json.dumps(
        {"source": "tpose", "rig": "humanoid", "mode": "sequence",
         "entries": entries}, indent=1))
    return run, entries


@pytest.fixture
def editor_run():
    made = []

    def make(tag, pose_cfg):
        run, entries = _run(tag, pose_cfg)
        made.append(run)
        return run, entries

    yield make
    for run in made:
        shutil.rmtree(run, ignore_errors=True)


def test_an_editor_save_renders_exactly_as_the_stage_would(root, editor_run):
    run, entries = editor_run("editor_match",
                              {"size": 256, "fill": 0.8, "thickness": 0.02,
                               "depth_scale": 1.3})

    # The pipeline's own derivation, reproduced independently. run.py copies
    # the raw config into the run dir, so styles and globals re-layer here.
    raw = yaml.safe_load((run / "config.yaml").read_text())
    styled, _ = styles.layer(root, raw, picks=raw.get("style_picks"))
    ctx = Context(root=root, outdir=run, config=settings.effective(root, styled),
                  run_id=run.name)
    want_dir = run / "_expected"
    want_dir.mkdir()
    want = pose_stage.render_entries(ctx, copy.deepcopy(entries), want_dir)

    save_poses({"run_id": run.name, "entries": copy.deepcopy(entries)})
    got = run / "00_pose" / "skeleton_000.png"

    assert got.exists(), "editor save wrote no skeleton"
    assert got.read_bytes() == want[0].read_bytes(), \
        "editor re-render differs from the stage's render"


def test_a_corrupt_manifest_is_rebuilt_not_left_failing(editor_run):
    # artifacts.json truncated mid-write (a crash) is valid JSON up to a
    # point, so json.loads() raises JSONDecodeError - a ValueError subclass,
    # not NotFound/Invalid. save_poses must still recover from it: this is
    # the "no usable manifest" case the except block exists for, and the
    # most likely real one, more likely than the file being absent outright.
    run, entries = editor_run("corrupt_manifest",
                              {"size": 256, "fill": 0.8, "thickness": 0.02})
    (run / "artifacts.json").write_text('{"completed": ["pose"], "artif')

    result = save_poses({"run_id": run.name, "entries": copy.deepcopy(entries)})

    assert result["manifest"] == "rebuilt", \
        "a truncated manifest should be rebuilt, not surfaced as a failure"


def test_pose_fill_reaches_the_editors_re_render(editor_run):
    made = []
    for tag, fill in (("fill_off", 0.0), ("fill_on", 0.85)):
        run, entries = editor_run(tag, {"size": 256, "fill": fill,
                                        "thickness": 0.02})
        save_poses({"run_id": run.name, "entries": copy.deepcopy(entries)})
        made.append(run / "00_pose" / "skeleton_000.png")
    assert made[0].read_bytes() != made[1].read_bytes()
