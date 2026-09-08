# Asset types become data

**Landed 2026-09-08.** All sections are implemented. Two things the design got
wrong are corrected in place below, marked **Corrected**.

Design for authoring a new asset type — a rail workspace — from the web UI.
Written before anything moves, so the reasoning survives the diff.

Today `module` is a Python dict in `generation/schema.py`, four entries, two of
them `available: False`. A person can pick one and cannot make one.

---

## 1. What `module` actually is, measured

Measured 2026-09-08 by grep across `pipeline/`, not by reading the docs.

| consumer | what it does with `module` | kind |
|---|---|---|
| `schema.fields_for` | filters `ConfigField.modules` — which knobs appear | presentation |
| `schema._render` → `help_for` | per-module rewording of help text | presentation |
| `schema.MODULES` | rail cell label / detail / blurb / `available` | presentation |
| `looks/styles.py:236` | style sheets carry per-module setting overrides | data |
| `orchestration/queue.py:34` | job listing | reporting |
| `api/runs.py:137` | audit label, reported as `protocol` | reporting |
| **`geometry/props.py:57`** | **`module != "character_sheet"` → attach props** | **behaviour** |

**One behavioural branch in the whole pipeline**, and it is already overridable
— `props.wanted` consults `props.enabled` and `props_enabled` first, and only
falls through to the module comparison when neither is set.

Stages are **not** module-scoped at all. Every config declares its own
`pipeline.stages`; no module has ever had a say in it. That is the gap this
design fills, and it is why a type can be data: there is almost no code behind
one.

### The string `"animation"` is written six times

`styles.py:227`, `styles.py:266`, `queue.py:34`, `configs.py:110`,
`runs.py:137`, `props.py:57` each spell the default module as a literal. Six
copies of one decision.

---

## 2. The constraint that decides the shape

`docs/OPEN.md` §3 says, of making `FIELDS` a projection of the stage registry:

> It requires `schema` to read the stage registry, which re-creates the
> `schema ↔ stage` cycle broken on 2026-08-26 — and as an *intra-group* cycle,
> the packaging test cannot catch it coming back.

Deriving "is this type usable" from "is every stage it names registered"
requires exactly that join. `schema.py` and `stage.py` are both in `generation/`,
and `test_packaging.py:71` walks cycles between **groups**, so an edge inside
`generation/` is invisible to it.

So the registry goes in `shared/`, which `test_packaging.py:27` forbids from
importing any sibling group. A module registry there **cannot** resolve
availability itself. The join is forced outward into `api/machine.py`, which
already does the same thing one line at a time:

```python
described["options"]["stage_names"] = sorted(available())
```

The placement is the enforcement. Nobody has to remember the rule.

---

## 3. The data

`library/modules/<key>.yaml`. Key comes from the filename, as it does for
`styles/`, `poses/`, `palettes/` and `props/`.

```yaml
label:   Portraits
detail:  one face, several expressions
blurb:   A character's head at several expressions. Judged on the face, so the
         body plan matters less and the palette matters more.
stages:  [pose, canonical, frames, palette, export]
extends: character_sheet
props:   false
```

| key | meaning | default |
|---|---|---|
| `label` | rail cell title | required |
| `detail` | rail cell subtitle | required |
| `blurb` | hover text | required |
| `stages` | the stage order a new pipeline of this type starts from | required |
| `extends` | another type whose scoped fields this one also shows | none |
| `props` | whether props attach — replaces `props.py:57` | `true` |

`library/` is "authored and versioned. Yours." (`AGENTS.md`), which is where a
type belongs: it is a thing a person writes, not a thing the program ships.

### The four builtins become four files

Seeded on first load, the way `settings.load_global` writes `_global.yaml` when
it is absent. One source of truth afterwards; no half-in-Python, half-on-disk
split where a person edits a file and the dict wins.

`tileset` and `object` keep their current stage lists, naming stages that do not
exist. That is deliberate — see §5.

---

## 4. `shared/modules.py`

```python
@dataclass
class ModuleSpec:
    key: str
    label: str
    detail: str
    blurb: str
    stages: list[str]
    extends: str = ""
    props: bool = True

DEFAULT = "animation"
```

Built through `shared/contracts.from_entry`, which already refuses a key the
dataclass cannot hold and says which keys are valid. A typo in a hand-written
type file is a named error, not a silently ignored line.

Held in `Registry("asset type", Scanned(...))`, so a malformed file lands in
`broken()` and a UI can say *"portrait.yaml could not be read"* rather than
omitting the type and leaving someone wondering where it went.

`DEFAULT` is the single home for the six copies of `"animation"`.

**Imports:** `yaml`, `.registry`, `.contracts`, `.errors`, `.paths`. Nothing
else, and `test_packaging.py:27` already fails if that changes.

---

## 5. Availability is derived, never set

`api/machine.py`:

```python
known = set(available())
for key, spec in modules.all().items():
    missing = [s for s in spec.stages if s not in known]
    out[key] = {**spec.rendered(), "available": not missing, "missing": missing}
```

`available` stops being a hand-maintained boolean. Two consequences:

- `tileset` becomes usable **the day** someone registers `tile_edges`. Nobody
  has to remember to flip a flag, and nobody can flip it early.
- A type may name a stage that does not exist yet. The rail shows it disabled
  with *"needs tile_edges"* instead of a flat "not built yet", so the type is a
  statement of intent and the missing stage is a named piece of work.

This is what the current `available: False` was standing in for, done so that it
cannot drift from the truth.

---

## 6. The behavioural branch becomes a lookup

`props.py:57`:

```python
return config.get("module", "animation") != "character_sheet"
```

becomes a read of `spec.props`. `geometry/` may import `shared/`, so no cycle.
After this, `module` has **no** behavioural branch left in the pipeline — it is
label, field scoping, style-sheet keying, and a default stage list.

---

## 7. Schema stops owning the list

**Corrected.** `ConfigSchema` does not take the module table at all. It only
ever used it to answer `describe()`, and that answer now needs the stage
registry, which `schema` must not reach — so `api/machine.py` builds the table
and merges it into the response instead. `ConfigSchema` keeps only `fields`,
and `fields_for` gains the resolved lineage as an argument. The rail's contract
widens rather than changes: the same `modules` key, plus `missing`.

`fields_for` resolves `extends`: a field scoped `modules=["animation"]` is shown
for a type declaring `extends: animation`. Without this a new type sees only the
124 unscoped fields and none of the 13 scoped ones (counted 2026-09-08), which reads as "the form
lost my pose settings".

Resolution follows the chain — `extends` may itself extend — carrying a visited
set, so a file naming itself, or two naming each other, is refused at load with
the cycle named rather than hanging.

---

## 8. Routes

| route | does |
|---|---|
| `GET /api/modules` | every type, with derived `available` and `missing` |
| `PUT /api/module?name=` | create or update one type file |

**No delete.** `server.py` has no `do_DELETE` and `routing.py` has no `delete()`
decorator; adding a verb touches the dispatch, the decorator set, and the route
contract test. That is its own change, not a rider on this one. Removing a type
is deleting a file.

**Corrected.** The design said `PUT` validates the stage order through the same
`runner.validate` that `configs.save_config` uses. That contradicts §5: an order
containing an unregistered stage cannot be built, so it cannot be checked at
all, and refusing it would forbid exactly the tileset case the feature exists
for. The rule is therefore: when every stage in the order exists, the order is
validated and an unrunnable one is refused; when any stage is missing, the order
is saved unchecked and the type reports `missing` and stays unavailable.

---

## 9. The coupling: a type with no pipeline is unreachable

**Landed 2026-09-08, ahead of the rest.** It stands alone — creating a pipeline
from the UI was impossible before this, whatever asset types do later — and
everything below depends on it.


`rail.js:pick` refuses to switch to a workspace with no config and toasts *"No
pipeline is set to X yet"*. A type created alone would therefore be invisible
the moment it existed.

So creating a type also creates its first pipeline, from the type's `stages`.
`PUT /api/config` already writes a new file when the target is absent, so this
needs no new backend — only a UI that calls it.

### While the config index is open

`rail.js:indexConfigModules` fetches **every config in full** — raw text,
effective merge, style record — to learn each one's single-word `module`, on
every boot. `GET /api/configs` returned bare strings; it returns
`[{name, module, error}]` instead, and the fan-out disappears. `error` carries
the parse failure of a config that will not load, so it stays in the picker
where someone can find it to repair rather than vanishing.

`shared/settings.DEFAULT_MODULE` landed here too, collapsing the six literal
`"animation"` fallbacks from §1. It moves to `shared/modules.py` with §4.

---

## 10. Front end

- Rail gains a `+ New type` cell. Unavailable cells show `⚠ needs tile_edges`
  rather than "not built yet".
- The type editor is a **modal from the rail**, not a ninth tab. The rail is the
  primary axis; a tab for editing the axis inverts that, and `main.js` already
  carries eight.
- The stage picker reuses `features/stages.js` — `orderProblems` and `autoOrder`
  exist and already mirror the Python check. No second implementation.

---

## 11. Tests

| claim | check |
|---|---|
| a type file with an unknown key is refused by name | `tests/unit/test_modules.py` |
| a malformed type file is `Broken`, not missing | same |
| `shared/` still imports nothing | `test_packaging.py:27`, already exists |
| `available` follows the stage registry | api test: register a stage, availability flips |
| a type naming a missing stage reports which | api test |
| `extends` shows the parent's scoped fields | schema test |
| `props` comes from the spec, not a string compare | `tests/unit/test_props.py`, extended |
| the rail renders a missing-stage type as disabled | `tests/frontend/test_frontend.mjs` |

`tests/golden/schema_fields.json` is keyed by module. The four builtins keep
their current keys and none declares `extends`, so the golden file is unchanged
— which is itself the check that this is a refactor with a feature on top, not a
rewrite of the settings surface.

---

## 12. Out of scope, deliberately

- **Deleting a type from the UI.** §8.
- **Authoring a stage.** A stage runs arbitrary Python and `canonical`/`frames`
  build ComfyUI graphs node by node. A type may *name* a missing stage; it
  cannot conjure one.
- **Pipeline duplicate / rename.** The starter pipeline in §9 is the minimum
  that makes a new type usable. The rest of the config lifecycle is separate.
- **`models.clip_vision`.** `comfy.py:242` reads it from `DEFAULT_GLOBAL`
  directly, so it is unconfigurable regardless of any form. Recorded in
  `docs/OPEN.md` §4.
