"""One declaration carrying its own bounds."""

from __future__ import annotations

import pytest

from pipeline.shared.contracts import ConfigField, Field
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
    # Rewriting `steps: 400` to 150 on save is not the same act as clamping.
    with pytest.raises(Invalid) as caught:
        _field().check(sent)
    assert caught.value.status == 400
    assert caught.value.detail.get("field") == "steps"
    assert "150" in caught.value.message


def test_a_per_group_mapping_survives_being_clamped():
    """depth.build reached the depth stage as None, so no config could widen a rig."""
    field = ConfigField(key="depth.build", label="Build", kind="float",
                        min=0.3, max=3.0, help="how heavy, as distinct from how tall")
    sent = {"torso": 1.35, "arms": 1.25}
    assert field.check(sent) == sent
    assert field.clamp(sent) == sent, "the mapping was discarded, not bounded"


def test_a_per_group_mapping_is_bounded_entry_by_entry():
    field = ConfigField(key="depth.build", label="Build", kind="float",
                        min=0.3, max=3.0, help="how heavy, as distinct from how tall")
    assert field.clamp({"torso": 9.0, "arms": 0.01}) == {"torso": 3.0, "arms": 0.3}


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
    # Twenty config fields reached the settings form with an empty (?).
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
    got = ConfigSchema(fields=[spec]).fields_for(None)[0]
    assert got["path"] == "frames.steps"
    assert got["type"] == "int"
    assert got["group"] == "Frames"
    assert "key" not in got and "kind" not in got


def test_config_field_has_no_wire_format_opinion_either():
    from pipeline.shared.contracts import ConfigField

    assert not hasattr(ConfigField, "as_dict")


def test_every_config_field_is_a_config_field():
    """The count guarded a migration, not a ceiling."""
    from pipeline.generation import schema
    from pipeline.shared.contracts import ConfigField

    assert len(schema.FIELDS) >= 137
    assert all(isinstance(f, ConfigField) for f in schema.FIELDS)
    keys = [f.key for f in schema.FIELDS]
    assert len(keys) == len(set(keys)), "a config path is declared twice"


def test_the_settings_form_is_unchanged_by_the_migration():
    """Every declared field renders, and none changed shape.

    One line per field, so the failure names the paths rather than diffing two
    eight-thousand-line dicts, and two branches declaring different fields do
    not collide.
    """
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "schema_golden", pathlib.Path("tools/schema_golden.py"))
    golden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(golden)

    want = golden.GOLDEN.read_text().splitlines()
    got = golden.lines()
    key = lambda row: tuple(row.split("\t", 2)[:2])   # noqa: E731
    before, after = {key(r): r for r in want}, {key(r): r for r in got}

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    assert not (added or removed or changed), (
        f"added {added}, removed {removed}, changed {changed}; "
        f"run tools/schema_golden.py --write when the change is intended")


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


def _settings_paths() -> dict[str, list[str]]:
    """Every `settings("path")` in the tree, and where it is read."""
    import ast
    import pathlib

    found: dict[str, list[str]] = {}
    for source in sorted(pathlib.Path("pipeline").rglob("*.py")):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "settings"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                found.setdefault(node.args[0].value, []).append(
                    f"{source}:{node.lineno}")
    return found


def test_every_settings_path_has_something_declared_at_it():
    import pipeline.stages  # noqa: F401
    from pipeline.generation.schema import SCHEMA

    undeclared = {
        path: where for path, where in _settings_paths().items()
        if SCHEMA.field(path) is None and not SCHEMA.defaults_under(path)
        and not [f for f in SCHEMA.fields if f.key.startswith(f"{path}.")]
    }
    assert not undeclared, (
        f"{undeclared} are read as settings and no field declares them, so "
        f"they have no bounds, no help, and no row in the settings form")


def test_a_field_named_for_a_stage_belongs_to_one_that_exists():
    import pipeline.stages  # noqa: F401
    from pipeline.generation.schema import FIELDS
    from pipeline.generation.stage import available

    stages = set(available())
    read = {p.split(".")[0] for p in _settings_paths()}
    elsewhere = {
        "cooling": "cooling.rest reads ctx.config directly",
        "detect": "refs/detect.py reads config['detect']",
        "pipeline": "run.py reads pipeline.stages before a Context exists",
    }
    heads = {f.key.split(".")[0] for f in FIELDS if "." in f.key}
    stray = sorted(heads - stages - read - set(elsewhere))
    assert not stray, (
        f"{stray} name neither a stage nor a known config block. A field "
        f"whose prefix matches nothing is a control that configures nothing.")

    barren = sorted(s for s in stages
                    if not [f for f in FIELDS if f.key.startswith(f"{s}.")])
    assert not barren, f"{barren} run with settings nobody can see or bound"


# Every path below is read by the pipeline and was refused by SCHEMA.check
# until 2026-09-11, because check ran only where the editor saved.
READ_BY_THE_PIPELINE = [
    "canonical.from_reference.enabled",
    "canonical.from_reference.start_at",
    "canonical.from_reference.weight_type",
    "canonical.prompt",
    "frames.prompt",
    "pose.views",
    "detect.host",
    "detect.keep_alive",
    "detect.attempts",
]


@pytest.mark.parametrize("path", READ_BY_THE_PIPELINE)
def test_a_path_the_pipeline_reads_is_a_path_a_config_may_set(path):
    from pipeline.generation.schema import SCHEMA

    assert SCHEMA.field(path) is not None, (
        f"{path} is read while a run builds its graph and no field declares "
        f"it, so the editor refuses to save a config that sets it")


def test_the_deprecation_guard_answers_before_the_validator_does():
    """`references.images` is a migration message, not a setting."""
    from pipeline.generation.schema import SCHEMA

    SCHEMA.check({"references": {"images": ["a.png"]}})
    assert SCHEMA.field("references.images") is None


def _shipped_configs():
    import pathlib

    return sorted(pathlib.Path("library/configs").rglob("*.yaml"))


@pytest.mark.parametrize("path", _shipped_configs(), ids=lambda p: p.stem)
def test_every_shipped_config_passes_the_check_a_run_now_makes(path):
    """106 of 110 failed this on 2026-09-11, all on one key in base_pixel."""
    import pathlib

    from pipeline.generation.schema import SCHEMA
    from pipeline.looks import styles
    from pipeline.shared import settings

    root = pathlib.Path(".")
    raw = settings.read_yaml(path)
    if path.stem == settings.GLOBAL_NAME:
        SCHEMA.check(raw)
        return
    merged, _ = styles.effective(root, raw, picks=raw.get("style_picks"))
    SCHEMA.check(merged)
