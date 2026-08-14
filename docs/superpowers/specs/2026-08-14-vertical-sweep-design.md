# The vertical sweep: declarations that carry their own policy

Design for the remainder of `docs/REFACTOR.md` §5. Written before anything moves,
so the reasoning survives the diff.

`REFACTOR.md` called §5 "the vertical sweep" and listed five coupled clusters.
Three landed. This specifies the rest, and corrects two things §5 got wrong.

---

## 1. Where the sweep actually stands

Measured 2026-08-14 with the scripts `REFACTOR.md` §7 prescribes, not by reading.

| § | move | status |
|---|---|---|
| 5a | six registries → one generic | **done** — `Source`, `Decorated`, `Scanned`, `Registry` all exist |
| 5b | exceptions carry their own status | structure **done**, conversion **stalled** |
| 5c | declarative specs converge on one base | **not started** |
| 5d | `server.py` behind a dispatch table | **done** — no longer in the top ten by size |
| 5e | two named duplications | partial |

### 5b never moved its own progress bar

§6 says "the count of remaining builtin raises is the progress bar". It was 79
at the start and is **80 now**. Total raises grew 114 → 131: the new ones went to
the taxonomy and the old builtins stayed exactly where they were.

The pattern is beautiful and half-installed. `server.py` dispatches on
`errors.status_for` in one block, but 32 `ValueError`, 19 `FileNotFoundError`
and 10 `KeyError` still reach it through `BUILTIN_STATUS` — the table
`errors.py:113` describes as scaffolding: *"Each entry that disappears is a
module whose failures have been named."* None have disappeared.

Where the 80 live:

| group | builtins | mostly |
|---|---|---|
| api | 18 | `FileNotFoundError` 10, `ValueError` 8 |
| stages | 15 | `RuntimeError` 7, `ValueError` 6 |
| geometry | 10 | `KeyError` 8 |
| refs | 9 | `ValueError` 5 |
| definitive | 8 | `ValueError` 7 |
| run.py / autopilot.py | 6 | `SystemExit` |
| looks, orchestration, shared, server.py, generation | 14 | mixed |

### The correction: the target was never zero

`REFACTOR.md` §7 says the count "should fall toward zero". That is wrong, and
acting on it would destroy the distinction that makes the pattern work.
`errors.py:9` draws the rule:

> a `ValueError` reaching the server is a bug, and a `PixelError` is a message
> to the user

If that holds, an internal invariant check **must stay** a builtin. All eight
`SystemExit` — five in `run.py`, one each in `autopilot.py`, `pixelize.py` and
`server.py` — are CLI exit codes and are correct as they are.
`registry.py`'s `NotImplementedError` is an abstract method. Converting those
would make every failure look like a user message, which is the mirror of the
bug being fixed.

The target is "no builtin raise on a path a user can reach", and that is a
different, checkable claim.

### The defect that panicked the laptop is sitting in the next slice

`definitive.Field` declared `min`/`max` that only ever reached the browser. That
is what let `upscale=64` past a control declaring `max=16`, and it is fixed as
of `c1129fb` with `Field.clamp()`.

`schema.FIELDS` is **137 raw dicts** with the same declarations — 77 carry
`min`, 77 carry `max`, 14 carry `options` — consumed by the settings form and
enforced **nowhere**. `configs.py:45` writes `yaml.safe_dump(incoming)` straight
to disk. Nothing on the Python side reads `FIELDS` at all outside `schema.py`.

Same decision, made independently in three places. That is the vertical.

A second finding from the same measurement: **20 config fields carry no help
text** and reach the settings form that way — `canonical.height`, `frames.steps`,
`palette.file`, `export.scale`, `comfy.host`, `frames.sampler`,
`frames.scheduler`, `models.pixel_lora` among them. `definitive.Field` has a
test making that state impossible. `REFACTOR.md` §5c already said the others
should converge on it rather than the reverse.

---

## 2. The unifying idea

`errors.py` works because the declaration carries its own policy and the
boundary decides what to do with it. The exception knows its status; the handler
never guesses. Two more axes take the same move:

| axis | declaration | carries | boundary decides |
|---|---|---|---|
| failure | `PixelError` | `status` | — (shipped) |
| bounds | `Field` | `min` / `max` / `options` | clamp · reject · warn |
| shape | `Contract` | response schema | import-time · test-time |

Everything below is one of those three.

---

## 3. Stage 1 — name what a user can reach

### The checker

A new static check, run by `make check` alongside the existing undefined-names
pass. It builds a call graph outward from every route handler — the methods
`routing.py` decorates with `@get` / `@post` / `@put` on `BaseRouter` subclasses
— and flags any builtin raise reachable from one.

```
make check
  static
    no undefined names                      ok
    every user-reachable failure named
      pipeline/api/looks.py:88
        raise FileNotFoundError(...)
        reachable: GET /api/looks -> load() -> _read()
      1 unnamed failure on an HTTP path     FAIL
```

**The escape hatch is the design.** A builtin that should stay gets a marker
that *requires* a reason:

```python
raise SystemExit(2)   # not-a-message: CLI exit code, never reaches HTTP
```

A bare marker is a failure. "This is a bug, not a message" becomes a written
claim someone can disagree with, rather than an omission nobody can see.

Static reachability is approximate — dynamic dispatch and `getattr` defeat it.
It errs toward flagging: a false positive costs one marker, a false negative
costs a 500 in front of a user.

### The conversion

Convert the flagged set. Expected shape of the work, by what the type means:

| builtin | becomes | because |
|---|---|---|
| `FileNotFoundError` on a named thing | `NotFound(what, name, available=...)` | it already names the alternatives, which is the answer usually wanted |
| `KeyError` from a registry lookup | `NotFound` | `geometry`'s 8 are almost all this |
| `ValueError` on caller input | `Invalid(msg, field=...)` | 400, not 500 |
| `RuntimeError` from a stage | `Unavailable` or `Internal` per case | a dead service is not a defect |
| `SystemExit` in CLI entry points | unchanged, marked | correct as is |

`BUILTIN_STATUS` entries are deleted as their last raise site converts. The
checker is what stops the count regrowing, which is precisely what failed
between the last two measurements.

### Done when

- the checker is green in `make check` and fails on an introduced violation
- every surviving builtin on an HTTP path carries a reasoned marker
- `BUILTIN_STATUS` is smaller, and the entries removed are named in the commit

---

## 4. Stage 2 — one declaration base

### The shape

`pipeline/shared/contracts.py`, which depends on nothing in `pipeline/` — the
`shared/` rule from `REFACTOR.md` §4 ("defined by having no dependencies, not by
being useful").

```
Field           key label kind help default min max step options when
                ├─ clamp(value)   correct silently, return the corrected value
                ├─ check(value)   raise Invalid naming the field and the range
                └─ as_dict()      what a form needs

ConfigField(Field)   + group, modules, options_from
                     `path` is `key`, dotted

LayerField(Field)    + nothing yet
                     exists so the surface names itself, and so a bound added
                     for layers later has somewhere to go that is not the base
```

The same shape as the thing it is modelled on: the base carries the policy, the
leaves specialise.

`path` ↔ `key` and `type` ↔ `kind` are the same concepts under two names; the
base picks one of each. `kind` gains `textarea`, `styles`, `stages` from the
config side.

### Policy per surface

One declaration, three enforcement policies, each named at its boundary rather
than arrived at by accident:

| surface | policy | why |
|---|---|---|
| layer preview / apply | `clamp` | a dragged slider has no error surface; already shipped |
| `PUT /api/config`, `PUT /api/global` | `check` → `Invalid` | it is the user's YAML. Silently rewriting `steps: 200` to `150` on save means the file no longer says what they typed |
| `ctx.stage_config`, `opt()` | `clamp` + record | the job has to run; the correction goes into facts where it can be seen |

### The help invariant

`Field.__post_init__` refuses a field with no help. The 20 gaps then fail at
**import**, so writing them is part of this stage and not a follow-up. This is
`REFACTOR.md` §7's "every field explained | already a test for definitive;
extend to `schema.FIELDS`", enforced by construction rather than by a test.

### The safety mechanism

137 mechanical conversions is where this stage can quietly break the UI. Before
touching anything, capture `fields_for(module)` output for every module to a
golden file; assert it identical afterwards. The frontend contract becomes
*provably* unchanged rather than hopefully unchanged, and the conversion can
then proceed in groups without re-reasoning about the UI each time.

`fields_for()` keeps returning plain dicts. The frontend does not change in this
stage at all.

### Done when

- `schema.FIELDS` is `ConfigField(...)` and `definitive.Field` is `LayerField`
- the golden file is identical before and after
- all 20 help gaps are written
- a config save with an out-of-range value returns 400 naming the field
- `shared/contracts.py` imports nothing from `pipeline/`

---

## 5. Stage 3 — responses carry their shape

### The shape

A route declares what it returns:

```python
@get("/layers", "the layer catalogue and a starting stack", returns=LayerCatalogue)
```

At import, every route must carry a contract. A route without one fails process
start — the property `make check` already gives configs, extended to the API.

Contracts are dataclasses plus a small structural checker. No new dependency:
this codebase is stdlib HTTP with no framework, and a validator library would be
a larger import than the thing it validates.

### Where validation runs

**In tests, not at runtime.** A test enumerates every registered route, calls
it, and checks the response against its contract. This buys the guarantee
without putting a validator in the request path of a local single-user tool.

The alternative — validating on every response — costs latency on every request
to catch a class of bug that only appears when a handler changes, which is
exactly when tests run. Recorded here because it is a real trade and the
opposite choice is defensible if the API ever becomes something other people
call.

Import time still checks what it can: that every route has a contract, and that
each contract is well-formed.

### Done when

- every route carries a contract and startup fails without one
- one test covers every route's response shape
- adding a route without a contract fails before any request is served

---

## 6. Order, and what each stage is worth alone

1. **Stage 1** — completes a pattern already in the codebase. No new concepts.
   Worth it alone: bad input stops returning 500.
2. **Stage 2** — the largest diff and the one that closes the `upscale=64` class
   of defect on the config surface. Worth it alone even if stage 3 never happens.
3. **Stage 3** — depends on nothing in 1 or 2, but is cheapest last, because
   stage 1 settles what a route may raise and stage 2 settles what it may accept.

Each ships on its own. None needs the next to be useful.

---

## 7. Explicitly out of scope

- **`REFACTOR.md` §5e** — the `run_audit` model-default duplication and the
  `web/js` ↔ `bodyspace.py` cross-language twins. Both real, neither is a
  declaration-carries-its-policy problem, and folding them in would make this
  three unrelated things again.
- **Runtime response validation** — decided against above; revisit if the API
  gains callers other than this repo's own frontend.
- **Frontend changes** — stage 2 is invisible to it by construction, enforced by
  the golden file.
- **Renaming `pipeline/` to `backend/`** — argued against previously on its own
  merits and unaffected by any of this.

---

## 8. How each claim here can be checked

Every number above came from a script. The same scripts verify the work.

| claim | check |
|---|---|
| 80 builtin raises, 79 at the start | count `ast.Raise` by exception name |
| the target is not zero | all 8 `SystemExit` (`run.py` 5, `autopilot.py`, `pixelize.py`, `server.py`) are CLI exit codes, and `registry.py`'s `NotImplementedError` is an abstract method |
| 137 config fields, 77 with `min` | count keys across `schema.FIELDS` |
| nothing enforces those bounds | `FIELDS` has no reader outside `schema.py` |
| 20 fields have no help | `[f for f in FIELDS if not f.get("help")]`, and again after `fields_for()` |
| the frontend is unaffected by stage 2 | golden file of `fields_for()` per module |
