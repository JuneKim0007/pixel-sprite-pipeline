"""What the two GPU stage bodies decide, checked without a GPU."""

from __future__ import annotations

import json

import pytest

from pipeline.generation.comfy import ComfyError
from pipeline.generation.stage import get
from pipeline.looks import vocabulary
from pipeline.shared import colour as colour_mod


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
    # The name, not the hex: CLIP reads "#FF00FF" as punctuation and digits.
    assert colour_mod.name_for(vocabulary.BACKDROP) in prompt, \
        "the backdrop colour reaches the prompt"
    assert vocabulary.BACKDROP not in prompt, "a hex code reached the encoder"


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


def _identity_ref(tmp, name="hero.png"):
    from tests.flows.conftest import png
    from pipeline.refs import references as refs_mod

    image = tmp / name
    png(image)
    return refs_mod.Reference(path=image, role="identity", yaw=0.0, label="front")


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

    for term in ("a wolf", "woodcut", colour_mod.name_for(vocabulary.BACKDROP)):
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


def test_the_clip_vision_weight_is_the_configured_one(comfy_fake, frames_ctx):
    """`models.clip_vision` had no way to reach the graph."""
    get("frames")().run(
        frames_ctx(1, models={"clip_vision": "my-clip.safetensors"}), {})
    loaded = [i["clip_name"] for i in comfy_fake.inputs_of("CLIPVisionLoader")]
    assert loaded, "no CLIPVisionLoader in the graph"
    assert set(loaded) == {"my-clip.safetensors"}, loaded


def test_an_unset_weight_still_falls_back_to_the_default(comfy_fake, frames_ctx):
    get("frames")().run(
        frames_ctx(1, models={"ipadapter": "my-ip.safetensors"}), {})
    clip = [i["clip_name"] for i in comfy_fake.inputs_of("CLIPVisionLoader")]
    ip = [i["ipadapter_file"] for i in comfy_fake.inputs_of("IPAdapterModelLoader")]
    assert set(clip) == {"CLIP-ViT-H-14.safetensors"}, clip
    assert set(ip) == {"my-ip.safetensors"}, ip


def test_canonical_conditioning_windows_come_from_config(monkeypatch, tmp_path):
    """These were literals: identity end_at 1.0 and style end_at 0.8."""
    from pathlib import Path

    from pipeline.generation.stage import Context

    seen = []

    def spy(g, model, image, *, weight, weight_type, start_at, end_at, models):
        seen.append({"type": weight_type, "start_at": start_at, "end_at": end_at})
        return model

    from pipeline.generation import comfy

    monkeypatch.setattr(comfy, "apply_ipadapter", spy)

    cfg = {"canonical": {"style": {"end_at": 0.4, "start_at": 0.1},
                         "from_reference": {"end_at": 0.9}}}
    ctx = Context(root=Path("."), config=cfg, run_id="r", outdir=tmp_path)
    assert ctx.settings("canonical.style")["end_at"] == 0.4
    assert ctx.settings("canonical.style")["start_at"] == 0.1
    assert ctx.settings("canonical.from_reference")["end_at"] == 0.9


def test_the_declared_defaults_are_what_the_literals_were(tmp_path):
    """A new field must not move behaviour until someone moves it."""
    from pathlib import Path

    from pipeline.generation.stage import Context

    ctx = Context(root=Path("."), config={}, run_id="r", outdir=tmp_path)
    assert ctx.settings("canonical.style")["end_at"] == 0.8
    assert ctx.settings("canonical.style")["start_at"] == 0.0
    assert ctx.settings("canonical.from_reference")["end_at"] == 1.0


def test_identity_weight_falls_back_to_the_role_default(tmp_path):
    """Blank means "use the reference's own weight", not zero."""
    from pathlib import Path

    from pipeline.generation.stage import Context
    from pipeline.shared.config import opt

    ctx = Context(root=Path("."), config={}, run_id="r", outdir=tmp_path)
    from_ref = ctx.settings("canonical.from_reference")
    assert (opt(from_ref, "weight", None) or 0.8) == 0.8


def test_the_shape_weight_reaches_the_adapter_and_not_just_the_settings(
        comfy_fake, frames_ctx):
    """Resolving is not arriving. weight_composition resolved correctly and
    reached no node, because the call site was never given it."""
    get("frames")().run(frames_ctx(1, frames={
        "ip_adapter": {"weight_type": "style and composition",
                       "weight": 0.7, "weight_composition": 0.25}}), {})
    seen = [i.get("weight_composition")
            for i in comfy_fake.inputs_of("IPAdapterAdvanced")]
    assert seen, "no IPAdapterAdvanced in the graph"
    assert 0.25 in seen, seen


def test_a_run_keeps_the_references_it_was_shown(tmp_path):
    """characters/ and library/refs can both be cleared by a person, and without
    a copy history says a run happened but not what it was given."""
    import json
    from types import SimpleNamespace

    from PIL import Image

    from pipeline.generation.stage import Context
    from pipeline.stages.canonical import _record_references

    src = tmp_path / "front.png"
    Image.new("RGB", (8, 8), (20, 20, 20)).save(src)
    ref = SimpleNamespace(path=src, label="front", role="identity")

    ctx = Context(root=tmp_path, outdir=tmp_path / "run", config={})
    (tmp_path / "run").mkdir()
    rig = SimpleNamespace(name="humanoid", label="Humanoid")
    _record_references(ctx, [(0.0, ref)], rig)

    kept = tmp_path / "run" / "references"
    assert (kept / "front.png").is_file(), "the reference was not kept"
    said = json.loads((kept / "used.json").read_text())
    assert said["rig"] == "humanoid"
    assert said["references"][0]["label"] == "front"
    assert said["references"][0]["source"] == str(src)

    # Removing the original must not take the record with it.
    src.unlink()
    assert (kept / "front.png").is_file()


def test_keeping_nothing_writes_nothing(tmp_path):
    from types import SimpleNamespace

    from pipeline.generation.stage import Context
    from pipeline.stages.canonical import _record_references

    ctx = Context(root=tmp_path, outdir=tmp_path / "run", config={})
    (tmp_path / "run").mkdir()
    _record_references(ctx, [], SimpleNamespace(name="x", label="X"))
    assert not (tmp_path / "run" / "references").exists()


def test_the_record_lands_before_sampling_not_after(tmp_path):
    """A run that dies mid-sample still has to say what it was shown."""
    import pathlib

    src = pathlib.Path("pipeline/stages/canonical.py").read_text()
    at_record = src.index("_record_references(")
    at_sample = src.index("comfy.sample_and_save(")
    assert at_record < at_sample, "the record is written after the first sample"


def test_the_record_keeps_the_guide_and_the_joints_that_describe_it(tmp_path):
    """A reference alone does not say what the rig asked for, and a re-cut
    image orphans the annotation that named its joints."""
    import json
    from types import SimpleNamespace

    from PIL import Image

    from pipeline.generation.stage import Context
    from pipeline.stages.canonical import _record_references

    src = tmp_path / "front.png"
    Image.new("RGB", (8, 8), (20, 20, 20)).save(src)
    src.with_suffix(".png.rig.json").write_text('{"points": {}}')
    guide = tmp_path / "skeleton_000.png"
    Image.new("RGB", (8, 8), (0, 0, 0)).save(guide)

    ctx = Context(root=tmp_path, outdir=tmp_path / "run", config={})
    (tmp_path / "run").mkdir()
    ctx.artifacts["skeletons"] = [guide]
    _record_references(ctx, [(0.0, SimpleNamespace(path=src, label="front",
                                                   role="identity"))],
                       SimpleNamespace(name="humanoid", label="Humanoid"))

    said = json.loads((tmp_path / "run" / "references" / "used.json").read_text())
    assert said["guides"], "the rig's own guide was not kept"
    assert (tmp_path / "run" / "references" / "skeleton_000.png").is_file()
    assert said["references"][0]["annotation"], "the joints were not kept"
