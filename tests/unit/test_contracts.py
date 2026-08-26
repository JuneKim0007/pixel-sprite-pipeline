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


FORM_ONLY = {"compute.vram_mode"}


def _declared():
    import pipeline.stages  # noqa: F401
    from pipeline.generation.schema import FIELDS

    return sorted(f.key for f in FIELDS
                  if f.default is not None and f.key not in FORM_ONLY
                  and "." in f.key)


@pytest.mark.parametrize("path", _declared())
def test_the_form_s_default_is_what_the_pipeline_reads(path, tmp_path):
    from pipeline.generation.schema import SCHEMA
    from pipeline.generation.stage import Context

    head, *rest = path.split(".")
    block = Context(root=tmp_path, outdir=tmp_path, config={}).settings(head)
    for step in rest:
        assert isinstance(block, dict) and step in block, \
            f"{path} is offered by the form and never reaches the pipeline"
        block = block[step]
    assert block == SCHEMA.field(path).default


def test_the_form_only_settings_are_read_somewhere_other_than_python():
    import pathlib

    from pipeline.generation.schema import SCHEMA

    ctl = pathlib.Path("scripts/ctl.sh").read_text()
    for path in sorted(FORM_ONLY):
        assert SCHEMA.field(path) is not None, f"{path} is not a field"
        assert path.split(".")[-1] in ctl, \
            f"{path} reaches neither the pipeline nor ctl.sh"
