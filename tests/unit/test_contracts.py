"""One declaration carrying its own bounds.

The same shape as errors.py, one axis over: an exception carries the status it
means, and a field carries the bounds it declares. Before this, min and max were
declared on 137 config fields and enforced on none of them - the settings form
read them and the server took whatever arrived.
"""

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
    (30, 30),          # in range
    (150, 150),        # exactly the maximum
    (1, 1),            # exactly the minimum
    (400, 150),        # over
    (0, 1),            # under
    ("42", 42),        # JSON strings coerce
    (None, 30),        # absent falls back to the default
    ("abc", 30),       # unparseable falls back to the default
])
def test_clamp_corrects_silently(sent, want):
    assert _field().clamp(sent) == want


@pytest.mark.parametrize("sent", [400, 0, -1])
def test_check_refuses_instead_of_correcting(sent):
    # A config file is a person's own text. Rewriting `steps: 400` to 150 on
    # save means the file no longer says what they typed, which is a different
    # act from clamping a slider that has no error surface.
    with pytest.raises(Invalid) as caught:
        _field().check(sent)
    assert caught.value.status == 400
    assert caught.value.detail.get("field") == "steps"
    # The range belongs in the message: "out of range" without it sends the
    # reader back to the form to find out what the range was.
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
    # definitive.Field has enforced this from the start and a test asserts it;
    # 20 config fields reached the settings form with an empty (?) because
    # nothing enforced the same rule on their side.
    with pytest.raises(ValueError, match="help"):
        _field(help="")


def test_as_dict_carries_everything_a_form_needs():
    got = _field().as_dict()
    assert got["key"] == "steps"
    assert got["kind"] == "int"
    assert got["min"] == 1 and got["max"] == 150


def test_the_layer_catalogue_is_unchanged_by_the_migration():
    """The editor's form is built from this. A migration that changes its shape
    changes the UI, which is not what a migration is for."""
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


def test_the_settings_form_is_unchanged_by_the_migration():
    """137 dicts become 137 declarations. The frontend must not be able to tell.

    Captured before the migration and asserted after it, so this is proof rather
    than hope - the settings form is generated from exactly this shape, and a
    silently dropped key is a control that stops rendering.
    """
    import json
    import pathlib

    from pipeline.generation import schema

    want = json.loads(pathlib.Path("tests/golden/schema_fields.json").read_text())
    # None is not a valid JSON object key. json.dumps would normally convert it
    # to "null" during encoding, but sort_keys=True sorts the raw dict items
    # first - comparing None against the str keys of the other modules raises
    # TypeError before encoding gets the chance. Converting it ourselves keeps
    # capture (see the golden file's generation) and comparison identical.
    got = json.loads(json.dumps(
        {("null" if m is None else m): schema.fields_for(m)
         for m in [None, *schema.MODULES]},
        sort_keys=True, default=str))
    assert got == want


def test_contracts_depends_on_nothing_but_errors():
    """shared/ is defined by having no dependencies, not by being useful.

    An import of another pipeline group here is the thing that turns a shared
    module into a cycle, and the import graph is the only place it shows.
    """
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
