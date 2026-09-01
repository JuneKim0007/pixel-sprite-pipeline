"""What the two GPU stage bodies decide, checked without a GPU.

`test_generate.py` covers the plan — which stages run, in what order. Nothing
covered the bodies, so `canonical.run` and `frames.run` were the two
most-changed methods in the tree and the two nothing could verify. The graph a
stage hands to ComfyUI is the whole of its decision; a recording client makes
that graph an assertion.
"""

from __future__ import annotations

import json

import pytest

from pipeline.generation.comfy import ComfyError
from pipeline.generation.stage import get
from pipeline.looks import vocabulary


# --------------------------------------------------------------- canonical

def test_canonical_writes_one_anchor_and_names_it(comfy_fake, stage_ctx, png):
    ctx = stage_ctx()
    out = get("canonical")().run(ctx, {})

    assert out["canonical"].name == "canonical.png"
    assert out["canonical"].exists()
    assert list(out["canonicals"]) == [90], "the default view is the side"


def test_canonical_prompts_with_subject_hint_style_and_backdrop(comfy_fake, stage_ctx):
    get("canonical")().run(stage_ctx(subject="a wolf", style="woodcut"), {})
    prompt = comfy_fake.prompt()

    assert prompt.startswith("a wolf")
    assert "woodcut" in prompt
    assert vocabulary.BACKDROP in prompt, "the backdrop colour reaches the prompt"


def test_canonical_drops_the_backdrop_when_it_is_off(comfy_fake, stage_ctx):
    get("canonical")().run(stage_ctx(background={"enabled": False}), {})
    assert vocabulary.BACKDROP not in comfy_fake.prompt()


def test_canonical_falls_back_to_the_default_style_when_it_is_blank(comfy_fake, stage_ctx):
    """A falsy style takes the default here. frames does NOT — see the pair below."""
    get("canonical")().run(stage_ctx(style=""), {})
    assert vocabulary.DEFAULT_STYLE in comfy_fake.prompt()


def test_canonical_refuses_to_start_without_comfyui(comfy_fake, stage_ctx):
    comfy_fake.alive_answer = False
    with pytest.raises(ComfyError, match="ComfyUI is not running"):
        get("canonical")().run(stage_ctx(), {})


def test_canonical_conditions_on_a_skeleton_when_one_exists(comfy_fake, stage_ctx, skeletons, pose_entries):
    ctx = stage_ctx()
    ctx.artifacts["skeletons"] = skeletons()
    ctx.artifacts["pose_frames"] = pose_entries(1, posed=False)
    get("canonical")().run(ctx, {})

    assert comfy_fake.count("ControlNetApplyAdvanced") >= 1
    assert any(u.startswith("skeleton_") for u in comfy_fake.uploads)


def test_canonical_without_a_skeleton_applies_no_control(comfy_fake, stage_ctx):
    get("canonical")().run(stage_ctx(), {})
    assert comfy_fake.count("ControlNetApplyAdvanced") == 0
    assert comfy_fake.uploads == []


def test_canonical_per_view_writes_one_anchor_per_pose_entry(comfy_fake, stage_ctx, skeletons, pose_entries, png):
    ctx = stage_ctx(canonical={"per_view": True})
    ctx.artifacts["pose_frames"] = pose_entries(3, posed=False)
    ctx.artifacts["skeletons"] = skeletons(3)
    out = get("canonical")().run(ctx, {})

    assert sorted(out["canonicals"]) == [0, 90, 180]
    assert len(comfy_fake.graphs) == 3, "one graph per anchor"
    written = sorted(p.name for p in out["canonical"].parent.glob("canonical_*.png"))
    assert written == ["canonical_front.png", "canonical_rear.png",
                       "canonical_side.png"]


def test_canonical_candidates_batch_into_one_graph(comfy_fake, stage_ctx, png):
    ctx = stage_ctx(canonical={"candidates": 3, "batch_candidates": True})
    out = get("canonical")().run(ctx, {})

    assert len(comfy_fake.graphs) == 1, "batching asks once"
    assert comfy_fake.inputs_of("EmptyLatentImage")[0]["batch_size"] == 3
    extra = sorted(p.name for p in out["canonical"].parent.glob("candidate_*.png"))
    assert len(extra) == 2, "the first image is the anchor, the rest are candidates"


def test_canonical_candidates_one_at_a_time_walk_the_seed(comfy_fake, stage_ctx):
    ctx = stage_ctx(canonical={"candidates": 3, "batch_candidates": False,
                             "seed": 40})
    get("canonical")().run(ctx, {})

    seeds = [g["inputs"]["seed"]
             for graph in comfy_fake.graphs
             for g in graph.values() if g["class_type"] == "KSampler"]
    assert seeds == [40, 41, 42]


# ------------------------------------------------------------------ frames

def test_frames_writes_one_image_per_skeleton(comfy_fake, frames_ctx, png):
    out = get("frames")().run(frames_ctx(3), {})

    assert [p.name for p in out["frames"]] == ["frame_000.png", "frame_001.png",
                                              "frame_002.png"]
    assert all(p.exists() for p in out["frames"])
    assert len(comfy_fake.graphs) == 3


def test_frames_falls_back_to_the_default_style_when_it_is_blank(comfy_fake, frames_ctx):
    """The pair of the canonical test above; the two used to disagree here."""
    get("frames")().run(frames_ctx(1, style=""), {})
    assert vocabulary.DEFAULT_STYLE in comfy_fake.prompt()


def test_frames_refuses_to_start_without_comfyui(comfy_fake, frames_ctx):
    comfy_fake.alive_answer = False
    with pytest.raises(ComfyError, match="ComfyUI is not running"):
        get("frames")().run(frames_ctx(), {})


def test_frames_names_the_ipadapter_nodes_it_cannot_find(comfy_fake, frames_ctx):
    comfy_fake.missing_nodes = {"IPAdapterAdvanced"}
    with pytest.raises(ComfyError, match="IPAdapter_plus"):
        get("frames")().run(frames_ctx(), {})


def test_frames_anchors_every_frame_on_the_canonical(comfy_fake, frames_ctx, png):
    get("frames")().run(frames_ctx(2), {})
    assert comfy_fake.uploads.count("canonical.png") >= 1
    assert comfy_fake.count("IPAdapterAdvanced", index=0) >= 1


def test_frames_conditions_each_frame_on_its_own_skeleton(comfy_fake, frames_ctx):
    get("frames")().run(frames_ctx(2), {})
    loaded = [i["image"] for i in comfy_fake.inputs_of("LoadImage", index=1)]
    assert any("skeleton_001" in name for name in loaded), \
        "frame 1 must use skeleton 1, not skeleton 0"


# ------------------------------------------------- the pair, as one statement

def test_both_stages_report_a_dead_service_the_same_way(comfy_fake, stage_ctx, frames_ctx):
    """One sentence, one place. Two copies of it drifted once already."""
    comfy_fake.alive_answer = False
    messages = []
    for stage, ctx in (("canonical", stage_ctx()),
                       ("frames", frames_ctx())):
        with pytest.raises(ComfyError) as caught:
            get(stage)().run(ctx, {})
        messages.append(str(caught.value))
    assert messages[0] == messages[1]


def test_both_stages_build_the_same_prompt_from_the_same_config(comfy_fake, stage_ctx, frames_ctx):
    """Subject, rig hint, style and backdrop are assembled twice, identically."""
    get("canonical")().run(stage_ctx(subject="a wolf", style="woodcut"), {})
    canonical_prompt = comfy_fake.prompt()

    comfy_fake.graphs.clear()
    get("frames")().run(
        frames_ctx(1, subject="a wolf", style="woodcut"), {})
    frames_prompt = comfy_fake.prompt()

    for term in ("a wolf", "woodcut", vocabulary.BACKDROP):
        assert term in canonical_prompt and term in frames_prompt, term


def test_the_recorded_graph_is_json_serialisable(comfy_fake, stage_ctx):
    """A graph that cannot be serialised could never have reached ComfyUI."""
    get("canonical")().run(stage_ctx(), {})
    assert json.loads(json.dumps(comfy_fake.graphs[0]))


def test_canonical_uploads_each_skeleton_once(comfy_fake, stage_ctx, skeletons, pose_entries):
    """The conditioning was computed before the loop and again inside it, so
    every run pushed the same PNG to ComfyUI twice."""
    ctx = stage_ctx()
    ctx.artifacts["skeletons"] = skeletons(3)
    ctx.artifacts["pose_frames"] = pose_entries(3, posed=False)
    get("canonical")().run(ctx, {})

    assert len(comfy_fake.uploads) == len(set(comfy_fake.uploads)), \
        f"uploaded twice: {comfy_fake.uploads}"


def test_canonical_per_view_uploads_only_the_anchors_it_renders(comfy_fake, stage_ctx, skeletons, pose_entries, png):
    ctx = stage_ctx(canonical={"per_view": True})
    ctx.artifacts["skeletons"] = skeletons(3)
    ctx.artifacts["pose_frames"] = pose_entries(3, posed=False)
    get("canonical")().run(ctx, {})

    assert comfy_fake.uploads == ["skeleton_000.png", "skeleton_001.png",
                                  "skeleton_002.png"]


def test_both_stages_read_a_blank_style_the_same_way(comfy_fake, stage_ctx, frames_ctx):
    """canonical used `or`, frames used `opt`: falsy against missing."""
    get("canonical")().run(stage_ctx(style=""), {})
    canonical_prompt = comfy_fake.prompt()
    comfy_fake.graphs.clear()
    get("frames")().run(frames_ctx(1, style=""), {})

    assert (vocabulary.DEFAULT_STYLE in canonical_prompt) is \
           (vocabulary.DEFAULT_STYLE in comfy_fake.prompt())
