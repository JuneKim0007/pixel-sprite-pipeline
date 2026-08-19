from __future__ import annotations

import pytest

from pipeline.generation.schema import fields_for
from pipeline.generation.stage import Context, available, opt

REGISTRY = available()


def test_no_stage_requires_what_nothing_produces():
    required = {r for s in REGISTRY.values() for r in s.requires}
    produced = {p for s in REGISTRY.values() for p in s.produces}
    assert not required - produced


def test_gpu_stages_are_marked():
    gpu = {n for n, s in REGISTRY.items() if s.resource == "gpu"}
    assert gpu == {"canonical", "frames"}


def test_blank_yaml_key_falls_back_to_the_default():
    assert opt({"size": None}, "size", 1024) == 1024


@pytest.mark.parametrize("key,want", [("size", 1024), ("fill", 0.8), ("view", "side")])
def test_stage_config_layers_defaults_under_the_config(root, key, want):
    ctx = Context(root=root, outdir=root,
                  config={"pose": {"size": None, "fill": 0.8}})
    assert ctx.stage_config("pose")[key] == want


def test_an_unregistered_stage_has_no_defaults(root):
    ctx = Context(root=root, outdir=root, config={})
    assert ctx.stage_config("nosuchstage") == {}


def test_a_mutable_default_is_not_shared_between_reads(root):
    ctx = Context(root=root, outdir=root, config={})
    ctx.stage_config("pose")["llm"]["host"] = "poisoned"
    assert "host" not in ctx.stage_config("pose")["llm"]


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


def test_the_rig_resolves_once_and_is_cached(root):
    ctx = Context(root=root, outdir=root, config={"rig": "spider"})
    first, second = ctx.rig(), ctx.rig()
    assert first is second, "rig resolved twice"
    assert first.name == "spider"
