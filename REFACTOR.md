# Backend restructuring

A probe of the backend as it stands, what the measurements say, and the moves
that follow. Written before anything is moved so the reasoning survives the
diff.

12,895 lines across 46 modules. `pipeline/` is flat: 30 modules in one
namespace with no grouping, so the only way to know what belongs with what is
to read the imports.

---

## 1. The finding that drives everything else

`pipeline/stage.py` is the base contract every stage implements. It imports
`annotate`, `detect`, `references`, `rigs` and `settings`.

That is inverted, and it has a measurable cost:

```
props  ->  stage  ->  detect  ->  llm
```

`props.py` describes where a sword points. It imports `stage` for `opt()`, a
ten-line config reader, and by doing so transitively depends on an LLM client.
Ten of eighteen modules in the package end up reachable from it.

The cause is that `stage.py` is three unrelated things in one file:

| in stage.py | what it is | lines |
|---|---|---|
| `opt()` | reads a config key, treating a blank YAML value as absent | 10 |
| `Stage`, `Resource`, `register`, `get`, `available` | the contract and its registry | 54 |
| `Context` | a service locator that resolves rigs, references and proportions | 128 |

`Context` is what drags the world in. Every consumer that only wants `opt` or
`Stage` pays for it.

**The import counts confirm which pieces are actually shared.** `bodyspace` is
imported by 12 modules, `stage` by 11, and nothing else exceeds 6. Those two
plus the leaves that depend on nothing inside the package are the real kernel:

```
artifacts  comfy  cooling  files  palettes  pixelize  rigs  settings  stylelog
definitive/layers  definitive/run
```

---

## 2. Three filesystem scanners, one shape

`palettes.discover`, `styles.discover` and `props.discover` are the same
function written three times:

| | root | glob | parse | cached | on a bad file |
|---|---|---|---|---|---|
| palettes | `Path` | `**/*.hex` | `_parse` | no | skip silently |
| styles | `Path` | `*.yaml` + `*/style.yaml` | yaml | no | skip silently |
| props | any | `props/**/*.yaml` | yaml | no | skip silently |

Each rescans the disk on every call. Each swallows a malformed file, so a typo
in a palette makes it vanish rather than complain. `styles` alone raises on a
duplicate name; the other two let a later file win silently.

Meanwhile there are three *in-memory* registries with a third shape again:

| | populated by | read by |
|---|---|---|
| stages | `@register` decorator | `get`, `available` |
| rigs | a dict literal | `get`, `summaries` |
| layers | `@layer` decorator | `catalogue` |

Six registries, three implementations, no shared behaviour. Caching, error
reporting and duplicate handling are decided independently six times.

---

## 3. Exceptions are nominal

Six exception classes exist. Five subclass `RuntimeError` directly, one
subclasses `PermissionError`, and there is no common base.

They are barely used. Of 114 raise sites:

```
ValueError          39      builtin
FileNotFoundError   28      builtin
KeyError            12      builtin
SystemExit           8      builtin
RuntimeError         8      builtin
LLMError             5
PipelineError        4
ComfyError           3
StyleError           3
PathDenied           2
```

**79 of 114 raises are builtins.** So the server cannot dispatch on type and
instead repeats the same mapping three times:

```python
except FileNotFoundError as e: self._error(404, str(e))
except (PermissionError, PathDenied) as e: self._error(403, str(e))
except Exception as e: self._error(500, f"{type(e).__name__}: {e}")
```

Three copies, and the third turns every domain error into a 500 with a stack
trace class name in the body. A user who names a palette that does not exist
gets a 500.

`except Exception` appears 14 times, more than any specific type.

---

## 4. The proposed shape

```
backend/
  src/
    shared/                  no dependency on any module
      errors.py              PixelError + the taxonomy, each with a status
      registry.py            Registry[T]: in-memory and filesystem-backed
      config.py              opt(), deep_merge, dotted paths
      paths.py               roots, safe_path, unique_name
      contracts.py           the declarative field/spec base

    modules/
      geometry/              rigs, bodyspace, openpose, depthmap, annotate,
                             autorig, softbody, props
      references/            references, detect
      looks/                 styles, palettes, stylelog, training
      generation/            comfy, stages/, runner, stage contract
      definitive/            layers, builtin, run, pixelize
      orchestration/         queue, cooling, autopilot, artifacts

    layer_connection/
      inbound/               what enters a module: config, references, files
      outbound/              what leaves: artifacts, manifests, events
      nodes/                 a step with declared inputs and outputs
      middleware/            cooling, retry, timing, error translation

  api/                       the HTTP surface, thin
  tests/
```

Two things this asserts, both supported by the measurements above.

**`shared/` is defined by having no dependencies, not by being useful.** The
leaf list in section 1 is the candidate set. Anything in `shared/` that grows an
import of a module is a mistake that the import graph will show.

**`layer_connection/` is where `Context` goes.** `Context` today is a service
locator: a stage asks it for a rig and it resolves one. Inverted, a node
*declares* it needs a rig and the connection layer supplies it. That is the
same move that fixed the stage ordering problem, which already validates
`requires` / `produces` before anything runs. The stage registry is a working
prototype of the node contract; it is just tangled with the service locator.

---

## 5. Coupled clusters, and the pattern each wants

The vertical sweep. Each of these is a place where the same decision is made in
several files.

### 5a. Six registries want one generic

```python
class Registry(Generic[T]):
    def __init__(self, name: str, *, source: Source[T], cache: bool = True): ...
    def get(self, key: str) -> T: ...          # raises NotFound, not KeyError
    def all(self) -> dict[str, T]: ...
    def summaries(self) -> list[dict]: ...
```

with two `Source` implementations, which is **strategy**:

```python
Decorated[T]           populated by a decorator at import time
Scanned[T](glob, parse) populated from disk, cached with an mtime check
```

Then `rigs = Registry("rig", source=Decorated())`, `palettes = Registry(
"palette", source=Scanned("palettes/**/*.hex", parse_hex))`. Duplicate names,
missing keys, caching and "what went wrong with this file" get decided once.

The measurable win: a malformed palette currently disappears. One
implementation makes it an error with a filename in it, everywhere, without six
edits.

### 5b. Exceptions want a taxonomy with the status attached

The server maps types to codes by hand in three places because the exceptions
carry no such information. Attach it:

```python
class PixelError(Exception):
    status = 500
class NotFound(PixelError):     status = 404
class Invalid(PixelError):      status = 400   # a bad value, not a bug
class Denied(PixelError):       status = 403
class Unavailable(PixelError):  status = 503   # ComfyUI is down
class Conflict(PixelError):     status = 409
```

The handler becomes one block, and 79 builtin raises get replaced by the
specific type at the call site. The rule that follows: **a `ValueError` reaching
the server is a bug, and a `PixelError` is a message to the user.** Today they
are indistinguishable, which is why bad input returns 500.

### 5c. Declarative specs want one base, and the spawn-time guarantee

Three declarations already exist in three shapes:

| | declares | consumed by |
|---|---|---|
| `schema.FIELDS` | 138 config fields | settings form, validation |
| `definitive.Field` | 18 layer fields | editor form, layer run |
| `Stage.requires/produces` | artifact contracts | runner validation |

`definitive.Field` is the newest and the closest to right — it is the only one
where a field cannot exist without a label and an explanation, and a test
asserts it. The others should converge on it rather than the reverse.

The contract-registry idea applies directly:

```python
class Contract:
    def __init__(self, name: str, model: type, *, status: int = 200): ...

CONTRACTS = [
    Contract("run", RunResponse),
    Contract("queue", QueueResponse),
]
```

The value is **validation at import time rather than at request time**. A
handler that returns a shape no contract describes fails when the process
starts, not when someone clicks. This is the same property `make check` already
gives configs, extended to the API.

### 5d. `server.py` is 1,307 lines and imports nothing that imports it

Seven domains plus a 500-line hand-rolled if-chain. Every backend feature lands
here; the last several commits all touched it. Split by domain behind a
dispatch table, so a route is data:

```python
ROUTES = {
    ("GET", "/api/runs"): runs.list_runs,
    ("POST", "/api/edit/preview"): editor.preview,
}
```

Which then makes 5c mechanical: a route entry can carry its contract.

### 5e. Two concrete duplications worth naming

- `run_audit` in `server.py` re-hardcodes the model default filenames that
  already live in `comfy.py`. Guaranteed to drift.
- `web/js/views.js` and `pipeline/bodyspace.py` are cross-language twins whose
  behaviour has already diverged once. Not fixable by moving files; worth a
  test that asserts they agree on a fixed set of inputs.

---

## 6. Order of work

Each step is verifiable on its own, and none of them needs the next one to be
useful.

1. **Split `stage.py`.** `opt` to `shared/config.py`, the registry to
   `shared/registry.py`, `Context` to `layer_connection/`. This is the
   highest-leverage move: it is what makes `props` stop depending on an LLM,
   and it can be done without moving any other file.
2. **`shared/errors.py`**, and replace the three server blocks with one.
   Convert raise sites module by module; the count of remaining builtin raises
   is the progress bar.
3. **`shared/registry.py`**, and move the six registries onto it one at a time.
4. **Move the modules** into the folders above. Mechanical once 1 to 3 are
   done, because the imports already point the right way.
5. **Split `server.py`** behind a dispatch table.
6. **Contracts**, once there are routes to attach them to.

A note on what does not move yet: `backend/` as a directory was examined
earlier and argued against, on the grounds that the split's usual rationale (an
independent frontend toolchain) does not exist here and ComfyUI sits at the
root regardless. That argument was about a directory rename. The reorganisation
above is about the import graph, and it is worth doing whether or not the
top-level folder is called `backend/`. Doing the graph first also makes the
rename trivial if it is wanted afterwards.

---

## 7. How each claim here can be checked

Everything above came from a script rather than a reading, and the same scripts
are how the work gets verified.

| claim | check |
|---|---|
| `props` reaches `llm` | walk the import graph for a path between them; it should not exist after step 1 |
| six registries | count `discover`/`REGISTRY`/`register` definitions; should reach one implementation |
| 79 builtin raises | count `ast.Raise` by exception name; should fall toward zero |
| `server.py` at 1,307 lines | line count per module |
| every field explained | already a test for `definitive`; extend to `schema.FIELDS` |
