"""One declaration carrying its own bounds."""

from __future__ import annotations

import pytest

from pipeline.shared.contracts import Field
from pipeline.shared.errors import Invalid


def _field(**kw):
    base = {"key": "steps", "label": "Steps", "kind": "int",
            "help": "How many denoising steps.", "default": 30,
            "min": 1, "max": 150}
    return Field(**{**base, **kw})


@pytest.mark.parametrize("sent,want", [
    (30, 30),
    (150, 150),
    (1, 1),
    (400, 150),
    (0, 1),
    ("42", 42),
    (None, 30),
    ("abc", 30),
])
def test_clamp_corrects_silently(sent, want):
    assert _field().clamp(sent) == want


@pytest.mark.parametrize("sent", [400, 0, -1])
def test_check_refuses_instead_of_correcting(sent):
    # Rewriting `steps: 400` to 150 on save means the file no longer says what they typed, which is a different act from clamping a slider that has no error surface.
    with pytest.raises(Invalid) as caught:
        _field().check(sent)
    assert caught.value.status == 400
    assert caught.value.detail.get("field") == "steps"
    assert "150" in caught.value.message


def test_check_passes_a_value_in_range():
    assert _field().check(30) == 30


def test_a_select_only_accepts_its_options():
    spec = _field(kind="select", options=[("euler", "Euler"), ("ddim", "DDIM")],
                  default="euler", min=None, max=None)
    assert spec.clamp("ddim") == "ddim"
    assert spec.clamp("nonsense") == "euler"
    with pytest.raises(Invalid):
        spec.check("nonsense")


def test_a_field_cannot_exist_without_an_explanation():
    # definitive.Field has enforced this from the start and a test asserts it; 20 config fields reached the settings form with an empty (?) because nothing enforced the [...]
    with pytest.raises(ValueError, match="help"):
        _field(help="")


def test_declared_carries_everything_a_form_needs():
    got = _field().declared()
    assert got["key"] == "steps"
    assert got["kind"] == "int"
    assert got["min"] == 1 and got["max"] == 150


def test_field_has_no_wire_format_opinion():
    assert not hasattr(Field, "as_dict")


def test_the_layer_catalogue_is_unchanged_by_the_migration():
    import json
    import pathlib

    from pipeline import definitive

    want = json.loads(pathlib.Path("tests/golden/layer_catalogue.json").read_text())
    got = json.loads(json.dumps(definitive.catalogue(), sort_keys=True, default=str))
    assert got == want


def test_a_layer_field_is_a_field():
    from pipeline.definitive.layers import Field as LayerFieldAlias
    from pipeline.shared.contracts import Field, LayerField

    assert issubclass(LayerFieldAlias, Field)
    assert LayerFieldAlias is LayerField


def test_a_config_field_speaks_the_form_s_dialect():
    from pipeline.generation.schema import ConfigSchema
    from pipeline.shared.contracts import ConfigField

    spec = ConfigField(key="frames.steps", label="Steps", kind="int",
                       help="How many denoising steps.", group="Frames",
                       min=1, max=150)
    got = ConfigSchema(fields=[spec], modules={}).fields_for(None)[0]
    assert got["path"] == "frames.steps"
    assert got["type"] == "int"
    assert got["group"] == "Frames"
    assert "key" not in got and "kind" not in got


def test_config_field_has_no_wire_format_opinion_either():
    from pipeline.shared.contracts import ConfigField

    assert not hasattr(ConfigField, "as_dict")


def test_every_config_field_is_a_config_field():
    from pipeline.generation import schema
    from pipeline.shared.contracts import ConfigField

    assert len(schema.FIELDS) == 137
    assert all(isinstance(f, ConfigField) for f in schema.FIELDS)


def test_the_settings_form_is_unchanged_by_the_migration():
    """137 dicts become 137 declarations."""
    import json
    import pathlib

    from pipeline.generation import schema

    want = json.loads(pathlib.Path("tests/golden/schema_fields.json").read_text())
    got = json.loads(json.dumps(
        {("null" if m is None else m): schema.fields_for(m)
         for m in [None, *schema.MODULES]},
        sort_keys=True, default=str))
    assert got == want


def test_contracts_depends_on_nothing_but_errors():
    import ast
    import pathlib

    source = pathlib.Path("pipeline/shared/contracts.py").read_text()
    reached = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            reached.add(node.module)
        elif isinstance(node, ast.Import):
            reached.update(a.name for a in node.names)

    outside = {m for m in reached
               if m.startswith("pipeline.") and not m.startswith("pipeline.shared")}
    outside |= {m for m in reached if m.startswith("..") }
    assert outside == set(), f"contracts.py reaches outside shared/: {outside}"


# path -> (stage file, the dict opt() reads, its key). Every entry is a field
# whose default is declared in the form AND read at a call site, so the two can
# drift apart silently — which is what this pins.
CALL_SITES = {
    "pose.llm.host": ("pose.py", "llm_cfg", "host"),
    "pose.llm.model": ("pose.py", "llm_cfg", "model"),
    "pose.llm.temperature": ("pose.py", "llm_cfg", "temperature"),
    "pose.llm.attempts": ("pose.py", "llm_cfg", "attempts"),
    "pose.llm.tolerance": ("pose.py", "llm_cfg", "tolerance"),
    "pose.llm.cache": ("pose.py", "llm_cfg", "cache"),
    "frames.controlnet.enabled": ("frames.py", "cn", "enabled"),
    "frames.controlnet.strength": ("frames.py", "cn", "strength"),
    "frames.controlnet.start_percent": ("frames.py", "cn", "start_percent"),
    "frames.controlnet.end_percent": ("frames.py", "cn", "end_percent"),
    "frames.ip_adapter.weight": ("frames.py", "ip", "weight"),
    "frames.ip_adapter.weight_type": ("frames.py", "ip", "weight_type"),
    "frames.ip_adapter.start_at": ("frames.py", "ip", "start_at"),
    "frames.ip_adapter.end_at": ("frames.py", "ip", "end_at"),
    "frames.ip_adapter.anchor": ("frames.py", "ip", "anchor"),
    "frames.ip_adapter.anchor_weight": ("frames.py", "ip", "anchor_weight"),
    "frames.ip_adapter.anchor_weight_type": ("frames.py", "ip", "anchor_weight_type"),
    "frames.ip_adapter.anchor_falloff": ("frames.py", "ip", "anchor_falloff"),
    "frames.ip_adapter.anchor_far_weight": ("frames.py", "ip", "anchor_far_weight"),
    "frames.ip_adapter.anchor_end_at": ("frames.py", "ip", "anchor_end_at"),
    "frames.depth_controlnet.strength": ("frames.py", "dcn", "strength"),
    "frames.depth_controlnet.start_percent": ("frames.py", "dcn", "start_percent"),
    "frames.depth_controlnet.end_percent": ("frames.py", "dcn", "end_percent"),
    "references.match.tolerance_degrees": ("frames.py", "match_cfg", "tolerance_degrees"),
    "references.match.far_weight": ("frames.py", "match_cfg", "far_weight"),
    "references.match.auto": ("frames.py", "match_cfg", "auto"),
    "canonical.controlnet.enabled": ("canonical.py", "cn", "enabled"),
    "canonical.controlnet.start_percent": ("canonical.py", "cn", "start_percent"),
}


def _opt_literals() -> dict:
    """Every `opt(block, "key", <literal>)` under stages/, by (file, block, key)."""
    import ast
    import pathlib

    found = {}
    for name in {site[0] for site in CALL_SITES.values()}:
        tree = ast.parse((pathlib.Path("pipeline/stages") / name).read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "opt" and len(node.args) == 3
                    and isinstance(node.args[0], ast.Name)
                    and isinstance(node.args[1], ast.Constant)):
                continue
            try:
                value = ast.literal_eval(node.args[2])
            except ValueError:
                continue          # a computed fallback is not a static default
            found[(name, node.args[0].id, node.args[1].value)] = value
    return found


@pytest.mark.parametrize("path", sorted(CALL_SITES))
def test_the_form_s_default_is_what_the_pipeline_actually_uses(path):
    # These reach the form through the field, not through Stage.DEFAULTS, which stops at one dot — so 34 nested fields showed no default at all and the form could not say what a blank meant.
    import pipeline.stages  # noqa: F401  (populates the stage registry)
    from pipeline.generation.schema import SCHEMA

    served = {f["path"]: f for f in SCHEMA.fields_for(None)}
    assert "default" in served[path], f"{path} shows no default in the form"
    used = _opt_literals().get(CALL_SITES[path])
    assert used is not None, f"no opt() literal for {path}; the call site moved"
    assert served[path]["default"] == used, (
        f"the form offers {served[path]['default']!r} and the pipeline uses "
        f"{used!r}")
