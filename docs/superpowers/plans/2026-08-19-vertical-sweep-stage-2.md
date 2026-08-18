# Vertical sweep, stage 2: one declaration base

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `Field` base carrying its own bounds and the code to enforce them, with `schema.FIELDS` and `definitive.Field` both built on it, and three enforcement policies chosen explicitly per surface.

**Architecture:** `pipeline/shared/contracts.py` holds `Field` (bounds + `clamp()` + `check()` + `as_dict()`), `ConfigField` (adds `group`, `modules`, `options_from`) and `LayerField` (adds nothing yet). The exact shape of `errors.py`: the base carries the policy, the leaves specialise. The frontend never changes — `fields_for()` keeps emitting the same dicts, and a golden file proves it.

**Tech Stack:** Python 3.12 stdlib, `dataclasses`. pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-14-vertical-sweep-design.md` (§4 is this stage)

## Global Constraints

- Interpreter `/Users/personal_jk/pixel-sweep/ComfyUI/.venv/bin/python`; the Makefile calls it `$(PY)`.
- **`pipeline/shared/` may not import from any other `pipeline/` group.** `contracts.py` may import only stdlib and `shared/errors.py`. A test enforces this.
- Bare `assert` only. Never a custom assert helper.
- Prefer `@pytest.mark.parametrize` over a loop inside one test.
- Assert behaviour, not source text.
- Comments state the measurement or reasoning behind non-obvious claims.
- `make check` and `make test` green before any task is called done.
- **The frontend must not change in this stage.** The golden file from Task 3 is the proof.

## Measured facts this plan is built on

Re-measured 2026-08-19, after stage 1:

```
schema.FIELDS       137 fields
  path 137 · label 137 · type 137 · group 137 · help 117
  min 77 · max 77 · step 59 · options 14 · options_from 13 · modules 13
  when 9 · free_numeric 1
  types: float 48 · int 29 · select 26 · bool 17 · text 10 · textarea 5 · styles 1 · stages 1

definitive.Field    key, label, kind, help, min, max, step, options, default, when
20 config fields carry NO help and reach the settings form that way.
```

Two shape notes that drive the design:

- `path` ↔ `key` and `type` ↔ `kind` are the same concepts renamed. The base picks
  one of each; `ConfigField.as_dict()` emits the config spelling so the frontend
  is untouched.
- **`default` is not declared in `FIELDS`.** `fields_for()` fills it from the
  stage's own `DEFAULTS` via `_declared_default()`. So `ConfigField` must tolerate
  having no default and let `fields_for()` keep filling it — do not move that
  logic into the dataclass.
- `help_for` is supported by `fields_for()` and used by zero fields today. Keep
  the support; it costs nothing and removing it is a separate decision.

---

### Task 1: The `Field` base

**Files:**
- Create: `pipeline/shared/contracts.py`
- Test: `tests/unit/test_contracts.py`

**Interfaces:**
- Consumes: `Invalid` from `pipeline/shared/errors.py`
- Produces: `Field` dataclass with `key, label, kind, help, default, min, max, step, options, when`; methods `clamp(value)`, `check(value)`, `as_dict()`; raises `Invalid` from `check`, and `ValueError` at construction when `help` is empty.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_contracts.py`:

```python
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
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_contracts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.shared.contracts'`

- [ ] **Step 3: Write the implementation**

Create `pipeline/shared/contracts.py`. Move the body of `clamp()` from
`pipeline/definitive/layers.py` (it already does the coercion correctly) and add
`check()` beside it. `check` and `clamp` must agree on what "in range" means —
write `check` in terms of the same comparisons rather than duplicating them, so
the two can never drift.

The module docstring must say why one declaration has three enforcement
policies, and name them: clamp for a dragged slider, check for a config save,
clamp-and-record for a pipeline read.

`__post_init__` raises `ValueError` when `help` is blank. Note in a comment that
this fires at **import**, so a field with no explanation cannot reach a form.

- [ ] **Step 4: Run the tests**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_contracts.py -v`
Expected: PASS

- [ ] **Step 5: Add the layering test**

Append to `tests/unit/test_contracts.py`:

```python
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
```

- [ ] **Step 6: Run everything and commit**

```bash
cd /Users/personal_jk/pixel-sweep && make check && make test
git add pipeline/shared/contracts.py tests/unit/test_contracts.py
git commit -m "A field that carries its own bounds, and the code to enforce them

min and max were declared on 137 config fields and enforced on none: the form
read them and the server took whatever the request sent. Three policies because
the surfaces genuinely differ - clamp a dragged slider, refuse a config save
rather than rewrite someone's YAML, clamp and record on a pipeline read because
the job still has to run."
```

---

### Task 2: `LayerField`, and `definitive` built on it

**Files:**
- Modify: `pipeline/shared/contracts.py` (add `LayerField`)
- Modify: `pipeline/definitive/layers.py` (Field becomes an alias for LayerField)
- Test: `tests/unit/test_contracts.py`, existing `tests/unit/test_memory_safety.py`

**Interfaces:**
- Consumes: `Field` from Task 1
- Produces: `LayerField(Field)` in `contracts.py`; `pipeline.definitive.layers.Field` is `LayerField`

This is the small migration, done first because its 18 fields are already typed
and already tested — if the base is wrong, it shows here cheaply.

- [ ] **Step 1: Capture the catalogue as a golden file**

The editor's layer catalogue is a public shape. Before changing anything:

```bash
cd /Users/personal_jk/pixel-sweep
ComfyUI/.venv/bin/python -c "
import json
from pipeline import definitive
print(json.dumps(definitive.catalogue(), indent=2, sort_keys=True, default=str))
" > tests/golden/layer_catalogue.json
```

Create `tests/golden/` if needed.

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/test_contracts.py`:

```python
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
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_contracts.py -v -k layer`
Expected: the golden test PASSES (nothing changed yet), the alias test FAILS.

- [ ] **Step 4: Implement**

Add `LayerField(Field)` to `contracts.py` with no extra attributes and a
docstring saying why it exists anyway: so the surface names itself, and so a
bound added for layers later has somewhere to go that is not the base.

In `pipeline/definitive/layers.py`, delete the local `Field` dataclass and its
`clamp`, and import `LayerField as Field` from `..shared.contracts`. Keep the
public name `Field` — `definitive/__init__.py` exports it and `builtin.py` uses
it 18 times.

- [ ] **Step 5: Run everything**

Run: `make check && make test`
Expected: green, including `tests/unit/test_memory_safety.py`'s existing clamping
tests, which now exercise the base class. If any of those fail, the base's
`clamp` differs from what `definitive` had — fix the base, not the test.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "definitive's Field becomes LayerField, and the catalogue is unchanged

The small migration first: 18 fields that were already typed and already tested,
so a wrong base shows here for the price of one file. A golden capture of
catalogue() proves the editor's form did not move."
```

---

### Task 3: Golden-file the config schema before touching it

**Files:**
- Create: `tests/golden/schema_fields.json`
- Test: `tests/unit/test_contracts.py`

**Interfaces:**
- Produces: a golden capture of `schema.describe()`'s `fields` for every module

This task changes no production code. It exists so Task 4's 137 conversions are
provably invisible to the frontend rather than hopefully invisible.

- [ ] **Step 1: Capture**

```bash
cd /Users/personal_jk/pixel-sweep
ComfyUI/.venv/bin/python -c "
import json
from pathlib import Path
from pipeline.generation import schema
out = {m: schema.fields_for(m) for m in [None, *schema.MODULES]}
print(json.dumps(out, indent=2, sort_keys=True, default=str))
" > tests/golden/schema_fields.json
wc -l tests/golden/schema_fields.json
```

- [ ] **Step 2: Write the test that pins it**

Add to `tests/unit/test_contracts.py`:

```python
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
    got = json.loads(json.dumps(
        {m: schema.fields_for(m) for m in [None, *schema.MODULES]},
        sort_keys=True, default=str))
    assert got == want
```

Note the JSON round-trip on both sides: `None` becomes the key `"null"`
consistently, so the comparison is like-for-like.

- [ ] **Step 3: Run it**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_contracts.py -v -k settings_form`
Expected: PASS (nothing has changed yet — that is the point).

- [ ] **Step 4: Commit**

```bash
git add tests/golden/schema_fields.json tests/unit/test_contracts.py
git commit -m "Capture the settings form's exact shape before migrating it

137 dicts are about to become 137 declarations. This is what makes that
provably invisible to the frontend instead of hopefully invisible."
```

---

### Task 4: `ConfigField`, and `schema.FIELDS` built on it

**Files:**
- Modify: `pipeline/shared/contracts.py` (add `ConfigField`)
- Modify: `pipeline/generation/schema.py` (137 dicts → `ConfigField(...)`)
- Test: `tests/unit/test_contracts.py`

**Interfaces:**
- Consumes: `Field` from Task 1
- Produces: `ConfigField(Field)` with `group`, `modules`, `options_from`, `free_numeric`; its `as_dict()` emits `path` (not `key`) and `type` (not `kind`) so the frontend contract holds.

**This is the largest task in the plan.** The golden file from Task 3 is the gate.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_contracts.py`:

```python
def test_a_config_field_speaks_the_form_s_dialect():
    """path and key are the same concept under two names, and so are type and
    kind. The base picks one; ConfigField emits the other, because the frontend
    reads `path` and `type` and this migration must not reach it."""
    from pipeline.shared.contracts import ConfigField

    spec = ConfigField(key="frames.steps", label="Steps", kind="int",
                       help="How many denoising steps.", group="Frames",
                       min=1, max=150)
    got = spec.as_dict()
    assert got["path"] == "frames.steps"
    assert got["type"] == "int"
    assert got["group"] == "Frames"
    assert "key" not in got and "kind" not in got


def test_every_config_field_is_a_config_field():
    from pipeline.generation import schema
    from pipeline.shared.contracts import ConfigField

    assert len(schema.FIELDS) == 137
    assert all(isinstance(f, ConfigField) for f in schema.FIELDS)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_contracts.py -v -k config_field`
Expected: FAIL — `ConfigField` does not exist.

- [ ] **Step 3: Implement `ConfigField`**

Add to `contracts.py`. It adds `group: str`, `modules: list[str]`,
`options_from: str`, `free_numeric: bool`. Its `as_dict()` starts from the base's
and renames `key`→`path`, `kind`→`type`.

`kind` must accept all eight config values: `float int select bool text textarea
styles stages`. Only the numeric kinds clamp against min/max; `styles` and
`stages` are opaque list-shaped values the form owns — say so in a comment.

- [ ] **Step 4: Convert the 137 declarations**

Mechanically rewrite `FIELDS` in `pipeline/generation/schema.py` from dicts to
`ConfigField(...)` calls. Preserve every value exactly. Do **not** add defaults —
`fields_for()` fills those from stage `DEFAULTS` via `_declared_default()`, and
moving that into the dataclass changes behaviour.

`fields_for()` must now call `.as_dict()` on each field before applying its
`help_for` override and default fill. Keep the rest of that function as it is.

**Convert in groups and run the golden test after each group** — a diff of a few
fields is findable, a diff of 137 is not.

Temporarily allow blank help: the 20 fields without it are Task 5's job, and
`Field.__post_init__` will refuse them. Pass `help="TODO"` for exactly those 20
and no others, so Task 5 has a precise list to work from. Record the 20 paths in
your report.

- [ ] **Step 5: Run the golden test**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_contracts.py -v`
Expected: `test_the_settings_form_is_unchanged_by_the_migration` PASSES.

If it fails, the diff tells you exactly which field and key moved. Fix the
declaration, never the golden file.

- [ ] **Step 6: Run everything and commit**

```bash
make check && make test
git add -A
git commit -m "137 config dicts become 137 declarations

Same shape out - the golden capture of fields_for() is byte-identical, so the
settings form cannot tell the migration happened. What changes is that the
bounds are now attached to something that can enforce them."
```

---

### Task 5: Write the 20 missing explanations

**Files:**
- Modify: `pipeline/generation/schema.py`
- Test: `tests/unit/test_contracts.py`

**Interfaces:**
- Consumes: `Field.__post_init__`'s help requirement from Task 1

These 20 fields reach the settings form with an empty `(?)` today:

```
canonical.height                     frames.steps
palette.file                         export.scale
comfy.host                           pose.llm.host
frames.sampler                       frames.scheduler
models.pixel_lora                    models.style_lora_strength
models.ipadapter                     canonical.controlnet.start_percent
frames.height                        frames.lora_strength
frames.negative                      frames.controlnet.union_type
frames.controlnet.start_percent      frames.depth_controlnet.start_percent
frames.ip_adapter.start_at           frames.ip_adapter.end_at
```

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_contracts.py`:

```python
def test_no_config_field_reaches_the_form_without_an_explanation():
    """A (?) that opens onto nothing is worse than no (?) at all - it promises
    an answer and does not have one. definitive.Field has enforced this from the
    start; this is the same rule reaching the other 137."""
    from pipeline.generation import schema

    blank = [f.key for f in schema.FIELDS if not f.help or f.help == "TODO"]
    assert blank == []
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_contracts.py -v -k explanation`
Expected: FAIL, listing the 20.

- [ ] **Step 3: Write the 20 explanations**

**This is writing, not transcription — do it properly.** For each field, read the
code that consumes it before writing about it. `grep -rn "<the dotted path's last
segment>" pipeline/stages/ pipeline/generation/` finds the consumer.

Match the voice of the existing 117: say what the control does, then what it
costs or when it matters. They lead with mechanism, not restatement of the label.
A good neighbour to imitate, from the same file:

> "Body plan. Decides joint layout and which ControlNet channel the skeleton can
> be sent to. Only 'humanoid' has a matching OpenPose model; every other rig uses
> scribble + depth, and 'blob' uses depth alone."

Do not write "The height." for `frames.height`. If you cannot find what a field
actually does, say so in your report rather than inventing a plausible sentence —
a confident wrong explanation is worse than the empty `(?)` it replaces.

- [ ] **Step 4: Run everything**

Run: `make check && make test`
Expected: green, and the golden test from Task 3 **now legitimately fails** for
these 20 fields, because their `help` changed from absent to written.

- [ ] **Step 5: Re-capture the golden file, deliberately**

This is the one place the golden file is allowed to move. Re-capture it, then
**diff the old against the new and confirm every changed line is a `help` key on
one of the 20** — nothing else may have moved:

```bash
cd /Users/personal_jk/pixel-sweep
cp tests/golden/schema_fields.json /tmp/schema_before.json
ComfyUI/.venv/bin/python -c "
import json
from pipeline.generation import schema
print(json.dumps({m: schema.fields_for(m) for m in [None, *schema.MODULES]},
                 indent=2, sort_keys=True, default=str))
" > tests/golden/schema_fields.json
diff /tmp/schema_before.json tests/golden/schema_fields.json | grep '^[<>]' | grep -v '"help"' 
```

That last command must print **nothing**. Paste its output into your report.

- [ ] **Step 6: Commit**

```bash
make check && make test
git add -A
git commit -m "Twenty config fields get the explanation they always claimed to have

Each shipped a (?) that opened onto nothing. The field base refuses a blank help
at import now, so this is enforced by construction rather than by remembering.
The golden file moved for exactly these 20 help keys and nothing else - verified
by diffing it."
```

---

### Task 6: A config save refuses out-of-range values

**Files:**
- Modify: `pipeline/api/configs.py`
- Test: `tests/api/test_routes.py`

**Interfaces:**
- Consumes: `ConfigField.check()` from Tasks 1 and 4
- Produces: `PUT /api/config` and `PUT /api/global` return 400 naming the field when a value is out of its declared range

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_routes.py`:

```python
def test_saving_a_config_out_of_range_is_refused_naming_the_field(http):
    # The declared bounds bind the API exactly as they bind the form. Until
    # this, `yaml.safe_dump(incoming)` wrote whatever arrived straight to disk.
    code, body = http.failure("/api/config?name=char_1",
                              {"config": {"frames": {"steps": 100000}}}, "PUT")
    assert code == 400
    assert body["kind"] == "invalid"
    assert "frames.steps" in str(body.get("detail", {})) + body["error"]


def test_saving_a_config_in_range_still_works(http):
    code, _ = http.failure("/api/config?name=char_1",
                           {"config": {"frames": {"steps": 30}}}, "PUT")
    assert code == 200
```

Read `tests/api/conftest.py` for `http.failure`'s exact signature, and check
`char_1`'s real shape in `library/configs/` before assuming the payload — use a
field that config actually has.

- [ ] **Step 2: Run it to make sure it fails**

Run: `make test T=tests/api/test_routes.py`
Expected: FAIL — the save succeeds with 200.

- [ ] **Step 3: Implement**

In `pipeline/api/configs.py`, before writing, walk the incoming config against
`schema.FIELDS` by dotted path and call `check()` on each value present. Use the
existing `get_path`/`set_path` helpers in `schema.py` rather than writing another
dotted-path walker.

A path the schema does not declare is passed through untouched — the schema does
not claim to be exhaustive, and refusing unknown keys would break every config
carrying a comment-only or experimental key.

Apply to both `save_config` and `_save_global`.

- [ ] **Step 4: Run everything and commit**

```bash
make check && make test
git add -A
git commit -m "A config save refuses a value outside its declared range

configs.py wrote yaml.safe_dump(incoming) straight to disk, so a form declaring
max=150 accepted 100000 and the pipeline read it back later. Refused rather than
clamped, because a config file is the user's own text and silently rewriting
steps: 100000 to 150 means the file stops saying what they typed."
```

---

### Task 7: A pipeline read clamps, and records that it did

**Files:**
- Modify: `pipeline/shared/config.py` (`opt`) and/or `pipeline/generation/stage.py` (`stage_config`)
- Test: `tests/flows/test_generate.py`

**Interfaces:**
- Consumes: `ConfigField.clamp()` from Tasks 1 and 4
- Produces: out-of-range values in an existing config are clamped at read, and the correction is recorded rather than silent

The third policy. A config already on disk may hold an out-of-range value —
written before Task 6 existed, or by hand. The job still has to run, so this
clamps rather than refusing, but a silent correction is how a run produces
something nobody can explain later.

- [ ] **Step 1: Write the failing test**

Add to `tests/flows/test_generate.py`:

```python
def test_an_out_of_range_config_value_is_clamped_and_said_out_loud(caplog):
    """A file written before the bounds were enforced still has to run. What it
    must not do is run differently from what it says without telling anyone."""
    from pipeline.generation import schema

    corrected, notes = schema.clamp_config({"frames": {"steps": 100000}})
    assert corrected["frames"]["steps"] == 150
    assert any("frames.steps" in n and "100000" in n for n in notes)


def test_a_config_in_range_is_returned_untouched():
    from pipeline.generation import schema

    corrected, notes = schema.clamp_config({"frames": {"steps": 30}})
    assert corrected["frames"]["steps"] == 30
    assert notes == []
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `make test T=tests/flows/test_generate.py`
Expected: FAIL — `schema` has no `clamp_config`.

- [ ] **Step 3: Implement `clamp_config`**

Add `clamp_config(cfg) -> tuple[dict, list[str]]` to `pipeline/generation/schema.py`.
It walks declared paths, clamps what is out of range, and returns the corrected
config plus one human-readable note per correction. It must not mutate its input.

Then call it where a run reads its config, and log the notes at WARNING so they
land in `var/logs` and in the run log. Find the read site by following
`Context.stage_config`; if the cleanest call site is the runner rather than
`opt()`, use that and say why in your report.

- [ ] **Step 4: Run everything and commit**

```bash
make check && make test
git add -A
git commit -m "A pipeline read clamps an out-of-range value and says so

The third policy, and the reason there are three: a slider has no error surface
so it clamps, a config save is the user's own text so it refuses, and a job that
is already running has to finish - so it clamps and writes down that it did."
```

---

## Stage 2 done when

- `pipeline/shared/contracts.py` holds `Field`, `ConfigField`, `LayerField` and imports nothing outside `shared/`
- `schema.FIELDS` is 137 `ConfigField`s; `definitive`'s `Field` is `LayerField`
- both golden files pass, and the schema one moved only for the 20 help keys
- no config field has blank help, enforced at import
- `PUT /api/config` with an out-of-range value returns 400 naming the field
- an out-of-range value already on disk is clamped at read with a logged note
- `make check` and `make test` green
