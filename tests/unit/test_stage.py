from __future__ import annotations

import pytest

from pipeline.generation.schema import fields_for
from pipeline.shared.errors import Invalid
from pipeline.generation.stage import Context, Stage, available, opt

REGISTRY = available()


def test_no_stage_needs_what_nothing_gives():
    from pipeline.generation.resources import RESOLVERS

    needed = {n for s in REGISTRY.values() for n in s.needs}
    given = {g for s in REGISTRY.values() for g in s.gives}
    # A need is answered by a sibling or by the run; the stage does not say which, so neither does this.
    assert not needed - given - set(RESOLVERS)


def test_gpu_stages_are_marked():
    gpu = {n for n, s in REGISTRY.items() if s.resource == "gpu"}
    assert gpu == {"canonical", "frames"}


def test_blank_yaml_key_falls_back_to_the_default():
    assert opt({"size": None}, "size", 1024) == 1024


@pytest.mark.parametrize("key,want", [("size", 1024), ("fill", 0.8), ("view", "side")])
def test_settings_layer_defaults_under_the_config(root, key, want):
    ctx = Context(root=root, outdir=root,
                  config={"pose": {"size": None, "fill": 0.8}})
    assert ctx.settings("pose")[key] == want


def test_an_unregistered_stage_has_no_defaults(root):
    ctx = Context(root=root, outdir=root, config={})
    assert ctx.settings("nosuchstage") == {}


def test_a_mutable_default_is_not_shared_between_reads(root):
    ctx = Context(root=root, outdir=root, config={})
    ctx.settings("pose")["llm"]["host"] = "poisoned"
    assert ctx.settings("pose")["llm"]["host"] != "poisoned"


def test_a_nested_block_keeps_the_siblings_the_config_left_alone(root):
    ctx = Context(root=root, outdir=root,
                  config={"frames": {"controlnet": {"strength": 0.1}}})
    cn = ctx.settings("frames")["controlnet"]
    assert cn["strength"] == 0.1
    assert cn["enabled"] is True and cn["end_percent"] == 0.55


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_schema_shows_the_defaults_a_stage_declares(name):
    by_path = {f["path"]: f for f in fields_for(None)}
    for key, value in (getattr(REGISTRY[name], "DEFAULTS", {}) or {}).items():
        field = by_path.get(f"{name}.{key}")
        if field is None:
            continue
        assert field.get("default") == value, f"{name}.{key}"


def test_pose_size_survives_into_the_schema():
    by_path = {f["path"]: f for f in fields_for(None)}
    assert by_path["pose.size"]["default"] == 1024


def test_a_declared_need_resolves_once_and_is_cached(root):
    ctx = Context(root=root, outdir=root, config={"rig": "spider"})
    first, second = ctx.need("rig"), ctx.need("rig")
    assert first is second, "rig resolved twice"
    assert first.name == "spider"
    assert "rig" not in ctx.artifacts, "a resource leaked into the resume channel"


def test_a_need_nothing_can_resolve_is_refused_by_name(root):
    ctx = Context(root=root, outdir=root, config={})
    with pytest.raises(Invalid, match="no resolver for 'weather'"):
        ctx.need("weather")


def test_a_stage_declaring_an_unresolvable_need_never_starts():
    # requires is checked before the run; needs was not checked at all, so a rig that could not be resolved surfaced minutes in rather than at the plan.
    from pipeline.generation import runner

    class Impossible(Stage):
        name = "impossible"
        needs = frozenset({"a_pony"})

        def run(self, ctx, prep):
            return {}

    with pytest.raises(runner.PipelineError, match="a_pony"):
        runner.validate([Impossible()], seeded=set())
