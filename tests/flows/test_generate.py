from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from pipeline.generation import runner
from pipeline.generation.stage import Context, Resource, get
from pipeline.geometry import rigs
from pipeline.orchestration import cooling

ORDER = ["pose", "depth", "canonical", "frames", "palette"]


@pytest.fixture(scope="module")
def batches():
    return runner.plan(runner.build(ORDER))


def _gpu_batches(batches, stop_after=None):
    executed = batches
    if stop_after:
        for i, b in enumerate(batches):
            if stop_after in [s.name for s in b.stages]:
                executed = batches[: i + 1]
                break
    return sum(1 for b in executed
               if any(s.resource == Resource.GPU for s in b.stages))


def test_the_plan_has_two_gpu_batches(batches):
    assert _gpu_batches(batches) == 2, "expected canonical and frames"


def test_a_gated_run_does_not_count_work_it_will_not_do(batches):
    assert _gpu_batches(batches, "canonical") == 1


@pytest.mark.parametrize("stop_after,rests", [("canonical", False), (None, True)])
def test_rests_fall_only_between_gpu_work_that_will_run(batches, stop_after, rests):
    estimate = cooling.estimate({}, _gpu_batches(batches, stop_after))
    assert (estimate > 0) is rests


def test_softbody_runs_as_a_stage_not_just_as_physics(root, tmp_path):
    frames = []
    for i in range(3):
        arr = np.zeros((64, 64, 3), dtype=np.uint8)
        arr[20:50, 26:38] = 200
        f = tmp_path / f"frame_{i:03d}.png"
        Image.fromarray(arr).save(f)
        frames.append(f)

    entries = [{"pose": {k: list(v) for k, v in rigs.HUMANOID.neutral.items()},
                "yaw": 90.0, "spec": 0} for _ in frames]

    ctx = Context(root=root, outdir=tmp_path, run_id="t", config={
        "rig": "humanoid",
        "softbody": {"nodes": [{"name": "belly", "anchor": "neck",
                                "offset": [0, 0.05, 0.19], "radius": 0.16,
                                "stiffness": 70, "damping": 4.5}]},
    })
    ctx.artifacts["frames"] = frames
    ctx.artifacts["pose_frames"] = entries

    stage = get("softbody")()
    produced = stage.run(ctx, stage.prepare(ctx))
    assert "soft_frames" in produced, "the stage returned the wrong artifact"
    assert len(produced["soft_frames"]) == len(frames)
    for path in produced["soft_frames"]:
        assert path.exists(), f"{path.name} was never written"


def _pixel_art(tmp_path, name, seed):
    rng = np.random.default_rng(seed)
    small = rng.integers(40, 220, (16, 16, 3), dtype=np.uint8)
    big = np.repeat(np.repeat(small, 8, 0), 8, 1)
    canvas = np.full((big.shape[0] + 16, big.shape[1] + 16, 3), 255, np.uint8)
    canvas[5:5 + big.shape[0], 3:3 + big.shape[1]] = big
    path = tmp_path / name
    Image.fromarray(canvas).save(path)
    return path


def test_the_lattice_is_found_once_per_run_not_once_per_frame(tmp_path, monkeypatch):
    from pipeline.definitive import pixelize as px
    from pipeline.stages import palette as palette_stage

    calls = []
    real = px.find_phase

    def counted(arr, factor):
        calls.append(factor)
        return real(arr, factor)

    monkeypatch.setattr(px, "find_phase", counted)
    monkeypatch.setattr(palette_stage, "find_phase", counted)

    canonical = _pixel_art(tmp_path, "canonical.png", 0)
    frames = [_pixel_art(tmp_path, f"frame_{i}.png", i + 1) for i in range(4)]
    ctx = Context(root=tmp_path, outdir=tmp_path,
                  config={"palette": {"factor": 8, "size": 8},
                          "compute": {"cpu_workers": 1}},
                  artifacts={"canonical": canonical, "frames": frames})

    stage = get("palette")()
    produced = stage.run(ctx, stage.prepare(ctx))

    assert len(produced["pixel_frames"]) == 4
    assert len(calls) == 1, f"the lattice was searched {len(calls)} times for 4 frames"
