# Vertical sweep, stage 1: name what a user can reach

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No builtin exception is raised on any code path reachable from an HTTP route handler, and a static check in `make check` keeps it that way.

**Architecture:** A static checker walks the call graph outward from every method decorated with `@get`/`@post`/`@put` on a `BaseRouter` subclass, and flags builtin raises reachable from one. Flagged sites convert to the `PixelError` taxonomy in `pipeline/shared/errors.py`. Sites that should stay builtin carry a `# not-a-message: <reason>` comment, and the reason is mandatory — so "this is a bug, not a message" becomes a written claim rather than an omission. The checker is wired into `make check` only once it is green, because a baseline allowlist is exactly what let the count creep from 79 to 80.

**Tech Stack:** Python 3.12 stdlib only (`ast`, `pathlib`, `re`). pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-14-vertical-sweep-design.md` (§3 is this stage)

## What a prototype of this checker already measured

The algorithm below was run against `pipeline/` before this plan was written, so
the numbers the tasks work toward are real rather than estimated:

```
route handlers found: 38
functions defined:    512
reachable from HTTP:  404          (79%)

unnamed failures on a reachable path: 67
  api 18 · stages 15 · refs 9 · geometry 8 · definitive 6
  orchestration 4 · shared 3 · looks 3 · generation 1
  ValueError 30 · FileNotFoundError 18 · KeyError 8 · RuntimeError 8 · other 3
```

**67 is the number Tasks 5–8 drive to zero.** It is 67 and not 72 because
reachability correctly excludes five sites in CLI-only paths — `pixelize.py`'s
`main`, and similar — which is the discrimination the whole approach rests on.

**The honest caveat:** 79% of functions are reachable, because resolution is by
bare name. This checker is therefore close to, but not the same as, "no builtin
raise anywhere in `pipeline/`". That is acceptable — it is a ratchet, and the
sites it excludes are the right ones — but if it proves too coarse in practice
the sharpening move is to resolve calls per-module using each file's imports
rather than against one global name table. Do not do that pre-emptively; do it
if a real false positive costs more than the marker it takes to silence.

## Global Constraints

- Python interpreter is `ComfyUI/.venv/bin/python`; the Makefile refers to it as `$(PY)`.
- `pipeline/shared/` may not import from any other `pipeline/` group. `tools/` may import from `pipeline/`.
- Assert behaviour, not source text. `AGENTS.md` forbids tests that grep code with `ast`/`inspect.getsource` — **the checker itself is exempt, because static analysis is its subject**, but its tests must exercise it through its public functions on fixture files, never by reading its own source.
- Never write a custom assert helper. Use bare `assert`.
- Prefer `@pytest.mark.parametrize` over a loop inside one test.
- Tests go in `tests/unit/` when a failure names one module.
- Commit messages say what was measured, not just what changed.
- Run `make check` and `make test` before claiming a task is done.
- The count of builtin raises is the progress signal. Record it in each conversion commit.

---

### Task 1: The checker finds functions reachable from a route handler

**Files:**
- Create: `tools/check_failures.py`
- Test: `tests/unit/test_check_failures.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Index(roots: list[Path])` with `.definitions: dict[str, list[tuple[Path, ast.FunctionDef]]]`
  - `entry_points(index: Index) -> list[tuple[Path, ast.FunctionDef]]`
  - `reachable(index: Index, entries) -> set[tuple[str, str]]` of `(path_str, function_name)`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_check_failures.py`:

```python
"""The checker that keeps user-reachable failures named.

Exercised through its public functions on fixture trees, never by reading its
own source: a checker that only works on this repo's current shape is not a
checker, it is a snapshot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import check_failures as cf


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


def test_a_function_called_by_a_handler_is_reachable(tmp_path):
    _tree(tmp_path, {"api.py": """
from .routing import get

class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        return load()

def load():
    return helper()

def helper():
    return 1

def unrelated():
    return 2
"""})
    index = cf.Index([tmp_path])
    found = {name for _, name in cf.reachable(index, cf.entry_points(index))}
    assert "listing" in found
    assert "load" in found
    assert "helper" in found, "reachability stopped at one hop"
    assert "unrelated" not in found, "everything was treated as reachable"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_check_failures.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.check_failures'`

- [ ] **Step 3: Implement the minimal code to make the test pass**

Create `tools/check_failures.py`:

```python
"""Every failure a user can reach must carry a name.

`pipeline/shared/errors.py` draws the rule this enforces: a ValueError reaching
the server is a bug, and a PixelError is a message to the user. That rule is
only worth anything if something checks it - the count of builtin raises was 79
when it was written and 80 a refactor later, because nothing did.

So: walk outward from every route handler and flag any builtin raise reachable
from one. Not every builtin raise - the target was never zero. A CLI exit code
and an abstract method are correctly builtins, and converting them would make
every failure look like a user message, which is the mirror of the bug.

Resolution is by bare name and therefore over-approximates: two functions called
`load` are treated as one. That is the right direction to be wrong in. A false
positive costs one marker with a reason attached; a false negative costs a 500
in front of someone.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTE_DECORATORS = {"get", "post", "put", "delete", "patch"}


class Index:
    """Every function defined under `roots`, by bare name."""

    def __init__(self, roots: list[Path]):
        self.definitions: dict[str, list[tuple[Path, ast.FunctionDef]]] = {}
        self.trees: dict[Path, ast.Module] = {}
        for root in roots:
            for path in sorted(Path(root).rglob("*.py")):
                try:
                    tree = ast.parse(path.read_text())
                except (OSError, SyntaxError):
                    continue
                self.trees[path] = tree
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.definitions.setdefault(node.name, []).append(
                            (path, node))


def _decorator_names(node: ast.FunctionDef) -> set[str]:
    names = set()
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def entry_points(index: Index) -> list[tuple[Path, ast.FunctionDef]]:
    """Every route handler: a method carrying @get / @post / @put."""
    return [(path, node)
            for defs in index.definitions.values()
            for path, node in defs
            if _decorator_names(node) & ROUTE_DECORATORS]


def _called_names(node: ast.FunctionDef) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def reachable(index: Index, entries) -> set[tuple[str, str]]:
    """Everything callable from an entry point, transitively."""
    seen: set[tuple[str, str]] = set()
    queue = list(entries)
    while queue:
        path, node = queue.pop()
        key = (str(path), node.name)
        if key in seen:
            continue
        seen.add(key)
        for name in _called_names(node):
            for target in index.definitions.get(name, []):
                if (str(target[0]), target[1].name) not in seen:
                    queue.append(target)
    return seen
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_check_failures.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/check_failures.py tests/unit/test_check_failures.py
git commit -m "A checker that can see which functions a request can reach

Resolution is by bare name, so two functions called load are treated as one.
Over-approximating is the right direction: a false positive costs a marker, a
false negative costs a 500 in front of someone."
```

---

### Task 2: The checker flags builtin raises in reachable code

**Files:**
- Modify: `tools/check_failures.py`
- Test: `tests/unit/test_check_failures.py`

**Interfaces:**
- Consumes: `Index`, `entry_points`, `reachable` from Task 1
- Produces: `Violation` dataclass with fields `path: Path`, `line: int`, `exception: str`, `function: str`; and `violations(index: Index) -> list[Violation]`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_check_failures.py`:

```python
def test_a_builtin_raise_a_request_can_reach_is_flagged(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        return load()

def load():
    raise FileNotFoundError("gone")
"""})
    found = cf.violations(cf.Index([tmp_path]))
    assert [(v.function, v.exception) for v in found] == [("load", "FileNotFoundError")]


def test_a_builtin_raise_no_request_can_reach_is_left_alone(tmp_path):
    _tree(tmp_path, {"cli.py": """
def main():
    raise SystemExit(2)
"""})
    assert cf.violations(cf.Index([tmp_path])) == []


def test_a_named_failure_is_not_a_violation(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise NotFound("look", "x")
"""})
    assert cf.violations(cf.Index([tmp_path])) == []
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_check_failures.py -v`
Expected: FAIL with `AttributeError: module 'tools.check_failures' has no attribute 'violations'`

- [ ] **Step 3: Implement the minimal code to make the test pass**

Add to `tools/check_failures.py` (after the imports, add `from dataclasses import dataclass`):

```python
# Builtins that mean "a defect", as opposed to the taxonomy which means "a
# message". SystemExit is here because a CLI raising one is correct and a route
# handler raising one is not - which is exactly the distinction reachability
# draws, so it is listed rather than exempted.
BUILTIN_FAILURES = {
    "ValueError", "FileNotFoundError", "KeyError", "RuntimeError", "TypeError",
    "PermissionError", "NotADirectoryError", "IsADirectoryError", "OSError",
    "TimeoutError", "IndexError", "SystemExit", "NotImplementedError",
    "Exception", "AttributeError", "ArithmeticError", "AssertionError",
}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    exception: str
    function: str


def _raised_name(node: ast.Raise) -> str | None:
    if node.exc is None:
        return None                      # a bare `raise` re-raises; not a site
    exc = node.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Attribute):
        return exc.attr
    return None


def violations(index: Index) -> list[Violation]:
    """Every builtin raise a request can reach."""
    live = reachable(index, entry_points(index))
    found: list[Violation] = []
    for path, defs in ((p, d) for name in index.definitions
                       for p, d in index.definitions[name]):
        if (str(path), defs.name) not in live:
            continue
        for node in ast.walk(defs):
            if not isinstance(node, ast.Raise):
                continue
            name = _raised_name(node)
            if name in BUILTIN_FAILURES:
                found.append(Violation(path, node.lineno, name, defs.name))
    return sorted(set(found), key=lambda v: (str(v.path), v.line))
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_check_failures.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/check_failures.py tests/unit/test_check_failures.py
git commit -m "The checker flags builtin raises a request can reach

A bare `raise` is a re-raise, not a site, so it is not flagged: the name was
already chosen wherever the exception came from."
```

---

### Task 3: The escape hatch requires a written reason

**Files:**
- Modify: `tools/check_failures.py`
- Test: `tests/unit/test_check_failures.py`

**Interfaces:**
- Consumes: `Violation`, `violations` from Task 2
- Produces: `MARKER` regex; `violations()` gains suppression behaviour; `Violation` gains `reason_missing: bool`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_check_failures.py`:

```python
def test_a_marker_with_a_reason_suppresses_the_flag(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise SystemExit(2)  # not-a-message: CLI exit code, never reaches HTTP
"""})
    assert cf.violations(cf.Index([tmp_path])) == []


def test_a_marker_with_no_reason_is_itself_the_failure(tmp_path):
    # The whole point of the hatch is that using it says something out loud.
    # A bare marker says nothing and would let the count creep back silently.
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise SystemExit(2)  # not-a-message:
"""})
    found = cf.violations(cf.Index([tmp_path]))
    assert len(found) == 1
    assert found[0].reason_missing is True


def test_a_marker_on_a_multiline_raise_is_found(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise ValueError(
            "a long message"
        )  # not-a-message: internal invariant, callers cannot trigger it
"""})
    assert cf.violations(cf.Index([tmp_path])) == []
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_check_failures.py -v`
Expected: FAIL — the first new test reports one violation instead of none

- [ ] **Step 3: Implement the minimal code to make the test pass**

Add `import re` to the imports. Add after `BUILTIN_FAILURES`:

```python
# The escape hatch, and the reason it demands a reason. "This is a defect, not
# a message" is a real claim about a call site and someone should be able to
# read it and disagree. A bare marker is an omission wearing a hatch.
MARKER = re.compile(r"#\s*not-a-message:(?P<reason>.*)$")
```

Change `Violation` to carry the flag:

```python
@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    exception: str
    function: str
    reason_missing: bool = False
```

Replace the body of the inner loop in `violations()` so the marker is consulted.
Add this helper above `violations()`:

```python
def _marker(source: list[str], node: ast.Raise) -> tuple[bool, str]:
    """Whether the raise carries a marker, and the reason given.

    Searched across the whole statement, because a raise with a long message
    wraps and the comment lands on the closing paren.
    """
    last = getattr(node, "end_lineno", node.lineno) or node.lineno
    for line_no in range(node.lineno, last + 1):
        if line_no - 1 >= len(source):
            break
        found = MARKER.search(source[line_no - 1])
        if found:
            return True, found.group("reason").strip()
    return False, ""
```

and rewrite the flagging block inside `violations()`:

```python
        source = path.read_text().splitlines()
        for node in ast.walk(defs):
            if not isinstance(node, ast.Raise):
                continue
            name = _raised_name(node)
            if name not in BUILTIN_FAILURES:
                continue
            marked, reason = _marker(source, node)
            if marked and reason:
                continue
            found.append(Violation(path, node.lineno, name, defs.name,
                                   reason_missing=marked))
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_check_failures.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/check_failures.py tests/unit/test_check_failures.py
git commit -m "The escape hatch demands a reason, and a bare one is the failure

'This is a defect, not a message' is a claim about a call site that someone
should be able to read and disagree with. A marker with nothing after it is an
omission wearing a hatch, so it is reported rather than honoured."
```

---

### Task 4: A command-line report, and the real baseline

**Files:**
- Modify: `tools/check_failures.py`
- Test: `tests/unit/test_check_failures.py`

**Interfaces:**
- Consumes: `violations` from Task 3
- Produces: `report(found: list[Violation]) -> str`; `main(argv: list[str]) -> int` returning 0 clean / 1 with violations

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_check_failures.py`:

```python
def test_the_report_names_the_file_line_and_exception(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise FileNotFoundError("gone")
"""})
    text = cf.report(cf.violations(cf.Index([tmp_path])))
    assert "api.py" in text
    assert "FileNotFoundError" in text
    assert "listing" in text


def test_exit_code_is_one_when_something_is_unnamed(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "x")
    def listing(self, req):
        raise ValueError("nope")
"""})
    assert cf.main([str(tmp_path)]) == 1


def test_exit_code_is_zero_when_clean(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "x")
    def listing(self, req):
        raise NotFound("look", "x")
"""})
    assert cf.main([str(tmp_path)]) == 0
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_check_failures.py -v`
Expected: FAIL — `module 'tools.check_failures' has no attribute 'report'`

- [ ] **Step 3: Implement the minimal code to make the test pass**

Append to `tools/check_failures.py`:

```python
def report(found: list[Violation]) -> str:
    """The violations, in the shape `make check` prints things."""
    lines = []
    for v in found:
        why = ("marker needs a reason after the colon" if v.reason_missing
               else f"reachable from a route, via {v.function}()")
        lines.append(f"  {v.path}:{v.line}\n"
                     f"    raise {v.exception} - {why}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import sys as _sys

    roots = [Path(a) for a in (argv if argv is not None else _sys.argv[1:])]
    if not roots:
        roots = [Path("pipeline")]
    found = violations(Index(roots))
    if found:
        print(report(found))
        print(f"  {len(found)} unnamed failure(s) on a path a request can reach")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())  # not-a-message: CLI exit code, this is not a route
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `ComfyUI/.venv/bin/python -m pytest tests/unit/test_check_failures.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Get the real baseline and check it against the prototype**

```bash
ComfyUI/.venv/bin/python tools/check_failures.py pipeline | tee /tmp/baseline.txt | tail -1
```

Expected: **67 unnamed failures**, distributed api 18 · stages 15 · refs 9 ·
geometry 8 · definitive 6 · orchestration 4 · shared 3 · looks 3 · generation 1.

If the number differs materially from 67, the implementation has diverged from
the prototype this plan was measured against — stop and find out why before
converting anything, because the conversion tasks are sized against that
distribution.

This number is the work remaining for Tasks 5–8. **Do not wire the checker into
`make check` yet** — a red build with a long allowlist is how the count crept
from 79 to 80 last time.

- [ ] **Step 6: Commit**

```bash
git add tools/check_failures.py tests/unit/test_check_failures.py
git commit -m "The checker reports, and names the baseline it has to clear

Deliberately not wired into make check yet: wiring it red would need an
allowlist, and an allowlist is what let the count go from 79 to 80."
```

---

### Task 5: Convert `pipeline/api` — the 18 that certainly reach a user

**Files:**
- Modify: `tests/api/conftest.py` (add a `failure()` helper — see Step 1)
- Modify: `pipeline/api/configs.py`, `pipeline/api/files.py`, `pipeline/api/runs.py`, `pipeline/api/poses.py`, `pipeline/api/looks.py`
- Test: `tests/api/test_routes.py`

**Interfaces:**
- Consumes: `NotFound`, `Invalid` from `pipeline/shared/errors.py`
- Produces: `http.failure(path, payload=None, method="GET") -> tuple[int, dict]` on the session `http` fixture; these routes now return 404/400 instead of 500

**Conversion table** — apply by what the raise *means*, not by its type:

| current | becomes |
|---|---|
| `runs.py:161` `raise FileNotFoundError(run_id)` | `raise NotFound("run", run_id)` |
| `files.py:19` `raise FileNotFoundError(run_id)` | `raise NotFound("run", run_id)` |
| `files.py:29` `raise FileNotFoundError("nothing to download for that selection")` | `raise NotFound("download", stage or run_id, hint="that run has no PNGs for that stage")` |
| `configs.py:18` `raise ValueError("invalid config name")` | `raise Invalid("a config name is letters, digits, dash and underscore", field="name")` |
| `configs.py:26,34` `raise ValueError(problem)` | `raise Invalid(problem, field="stages", hint="pass force to save it anyway")` |

- [ ] **Step 1: Add a `failure()` helper to the API client, then write the failing test**

The existing `status()` on the `http` fixture returns only a code and throws the
body away, so it cannot assert that a failure *names* anything. Add to the
`Client` class in `tests/api/conftest.py`, after `status()`:

```python
        def failure(self, path, payload=None, method="GET"):
            """The code AND the body. status() discards the body, which is
            most of what a good failure is."""
            try:
                if payload is None:
                    urllib.request.urlopen(host + path, timeout=20)
                else:
                    self.send(path, payload, method)
                return 200, {}
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
```

Then add to `tests/api/test_routes.py`:

```python
def test_a_missing_run_is_a_404_that_names_it(http):
    # GET /api/run?id=... — the detail route, which read runs_dir() and raised
    # a bare FileNotFoundError that reached the client as a 500.
    code, body = http.failure("/api/run?id=does_not_exist")
    assert code == 404
    assert body["kind"] == "not_found"
    assert "does_not_exist" in body["error"]


def test_a_bad_config_name_is_a_400_naming_the_field(http):
    code, body = http.failure("/api/config?name=has%20a%20space",
                              {"config": {}}, "PUT")
    assert code == 400
    assert body["kind"] == "invalid"
    assert body["detail"]["field"] == "name"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `make test T=tests/api/test_routes.py`
Expected: FAIL — status is 500 for both

- [ ] **Step 3: Convert the raise sites**

In `pipeline/api/files.py:19`:

```python
    if not run.is_dir():
        raise NotFound("run", run_id)
```

In `pipeline/api/files.py:29`:

```python
    if not sources:
        raise NotFound("download", stage or run_id,
                       hint="that run has no PNGs for that stage")
```

In `pipeline/api/configs.py:18`:

```python
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name or ""):
        raise Invalid("a config name is letters, digits, dash and underscore",
                      field="name")
```

In `pipeline/api/configs.py:26` and `:34`:

```python
        if problem and not body.get("force"):
            raise Invalid(problem, field="stages",
                          hint="pass force to save it anyway")
```

Work through every site the checker listed for `pipeline/api`. Add the imports
each module needs from `..shared.errors`.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `make check && make test`
Expected: all pass, 335+ backend tests

- [ ] **Step 5: Confirm the checker's count fell**

Run: `ComfyUI/.venv/bin/python tools/check_failures.py pipeline | tail -1`
Expected: the count is lower than the baseline from Task 4 by the number of api sites converted.

- [ ] **Step 6: Commit**

```bash
git add pipeline/api tests/api
git commit -m "api: 18 failures a user can reach now say what they are

A missing run returned 500 with a class name in the body; it is a 404 naming
the run. A config name with a space returned 500; it is a 400 naming the field.
Checker count: <before> to <after>."
```

---

### Task 6: Convert `pipeline/geometry` — `KeyError` was always `NotFound`

**Files:**
- Modify: `pipeline/geometry/bodyspace.py:372`, `:382`, `pipeline/geometry/rigs.py:678`, `pipeline/geometry/props.py:42,166,173`, `pipeline/geometry/softbody.py:35,65`
- Test: `tests/unit/test_rigs.py`, `tests/unit/test_props.py`

**Interfaces:**
- Consumes: `NotFound` from `pipeline/shared/errors.py`
- Produces: these lookups raise `NotFound` (404), and `NotFound` renders the alternatives automatically

These eight are the clearest win in the stage: they already hand-roll the "and
here are the alternatives" text that `NotFound.__init__` produces from
`available=`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_rigs.py`:

```python
def test_an_unknown_view_names_the_ones_that_exist():
    from pipeline.geometry import bodyspace
    from pipeline.shared.errors import NotFound

    with pytest.raises(NotFound) as caught:
        bodyspace.angle_for("sideways")
    assert caught.value.status == 404
    # The alternatives are the answer usually wanted, and NotFound builds them
    # rather than each call site writing its own sentence.
    assert "front" in caught.value.hint
```

Confirm the real function name at `pipeline/geometry/bodyspace.py:368` and use it.

- [ ] **Step 2: Run it to make sure it fails**

Run: `make test T=tests/unit/test_rigs.py`
Expected: FAIL — `KeyError` raised, not `NotFound`

- [ ] **Step 3: Convert the raise sites**

`pipeline/geometry/bodyspace.py:372` — the hand-built alternative list becomes
the one `NotFound` builds:

```python
    except ValueError:
        raise NotFound("view", str(view), available=list(VIEWS),
                       hint="a number of degrees, or one of: "
                            + ", ".join(f"{k} ({v:g}°)"
                                        for k, v in VIEWS.items())) from None
```

`pipeline/geometry/bodyspace.py:382`:

```python
    if unknown:
        raise Invalid(f"unknown joint(s): {sorted(unknown)}", field="overrides",
                      hint=f"this rig has: {', '.join(sorted(known))}")
```

Convert the remaining geometry sites the checker lists, choosing `NotFound` for
"you named a thing that does not exist" and `Invalid` for "the value you gave is
wrong".

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `make check && make test`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add pipeline/geometry tests/unit
git commit -m "geometry: eight KeyErrors were NotFound wearing a builtin

Each already hand-wrote the 'and here are the alternatives' sentence that
NotFound builds from available=. Checker count: <before> to <after>."
```

---

### Task 7: Convert `pipeline/definitive` and `pipeline/stages`

**Files:**
- Modify: the `definitive` and `stages` sites the checker lists
- Test: `tests/flows/test_pixel_editor.py`, `tests/flows/test_generate.py`

**Interfaces:**
- Consumes: `Invalid`, `Unavailable`, `Internal` from `pipeline/shared/errors.py`
- Produces: no new names

`stages`' seven `RuntimeError` need the judgement call: a dead service is
`Unavailable` (503, and the queue already treats that as "hold, do not fail the
job"), a broken invariant is `Internal` (500, and correctly so).

- [ ] **Step 1: Write the failing test**

Add to `tests/flows/test_pixel_editor.py`:

```python
def test_a_bad_hex_colour_is_a_message_not_a_defect():
    import numpy as np

    from pipeline import definitive
    from pipeline.shared.errors import Invalid

    stack = [{"layer": "palette", "id": "p0", "enabled": True,
              "config": {"file": "", "colour": "#zzzzzz"}}]
    # A half-typed hex is something the user is in the middle of doing, so it
    # must arrive as a message against that layer rather than a 500.
    _, facts = definitive.apply_stack(np.zeros((8, 8, 3), np.uint8), stack)
    errors = [layer.get("error", "") for layer in facts["layers"]]
    assert any("Invalid" in e for e in errors)
```

Adjust the layer key and config to a real one from `definitive.catalogue()`.

- [ ] **Step 2: Run it to make sure it fails**

Run: `make test T=tests/flows/test_pixel_editor.py`
Expected: FAIL — the recorded error names `ValueError`

- [ ] **Step 3: Convert the raise sites**

Replace each `ValueError` in `pipeline/definitive/` that the checker lists with
`Invalid(message, field=<the config key at fault>)`. For `pipeline/stages/`,
apply the judgement above and mark any genuine invariant:

```python
    raise RuntimeError("stage produced no frames")  # not-a-message: the runner validates this upstream, so reaching it is a defect
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `make check && make test`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add pipeline/definitive pipeline/stages tests
git commit -m "definitive, stages: a half-typed hex is a message, not a 500

Judgement recorded per site: a dead service is Unavailable so the queue holds
the job, a broken invariant stays a builtin with a reason. Checker count:
<before> to <after>."
```

---

### Task 8: Convert `refs`, `looks`, `orchestration`, `generation`, `shared`

**Files:**
- Modify: the remaining sites the checker lists across these five groups
- Test: `tests/unit/test_references.py`, `tests/flows/test_styles.py`, `tests/flows/test_queue.py`

**Interfaces:**
- Consumes: the taxonomy in `pipeline/shared/errors.py`
- Produces: no new names

- [ ] **Step 1: Write the failing test**

Add to `tests/flows/test_styles.py`:

```python
def test_naming_a_style_that_does_not_exist_lists_the_ones_that_do(tmp_path):
    from pipeline.looks import styles
    from pipeline.shared.errors import NotFound

    with pytest.raises(NotFound) as caught:
        styles.load(tmp_path, "no_such_style")
    assert caught.value.status == 404
```

Confirm `styles.load`'s real signature first and match it.

- [ ] **Step 2: Run it to make sure it fails**

Run: `make test T=tests/flows/test_styles.py`
Expected: FAIL

- [ ] **Step 3: Convert the remaining sites**

Work the checker's list for these five groups to zero, choosing the type by
meaning. `pipeline/shared/` may only use `pipeline/shared/errors.py` — that is
already true and must stay true.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `make check && make test`
Expected: all pass

- [ ] **Step 5: Confirm the checker is green**

Run: `ComfyUI/.venv/bin/python tools/check_failures.py pipeline`
Expected: no output, exit code 0

```bash
ComfyUI/.venv/bin/python tools/check_failures.py pipeline; echo "exit=$?"
```

- [ ] **Step 6: Commit**

```bash
git add pipeline tests
git commit -m "refs, looks, orchestration, generation: the checker reaches zero

Every failure a request can reach now carries a name or a written reason for
not carrying one. Checker count: <before> to 0."
```

---

### Task 9: Wire the checker into `make check`, and delete the dead scaffolding

**Files:**
- Modify: `Makefile` (the `lint` target, around line 100)
- Modify: `pipeline/shared/errors.py:113-122` (`BUILTIN_STATUS`)
- Test: `tests/unit/test_errors.py`

**Interfaces:**
- Consumes: `tools/check_failures.py` `main()` from Task 4
- Produces: `make check` fails on an unnamed user-reachable failure

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_errors.py`:

```python
def test_builtin_status_only_covers_what_still_raises_builtins():
    """The table is scaffolding, and scaffolding that outlives the building is
    just clutter that reads like policy. Every entry must earn its place."""
    import ast
    import pathlib

    from pipeline.shared import errors

    raised = set()
    for path in pathlib.Path("pipeline").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc
                if isinstance(exc, ast.Call):
                    exc = exc.func
                if isinstance(exc, ast.Name):
                    raised.add(exc.id)

    stale = {cls.__name__ for cls in errors.BUILTIN_STATUS} - raised
    assert stale == set(), f"BUILTIN_STATUS still maps types nothing raises: {stale}"
```

This is one of the architectural-invariant exceptions `AGENTS.md` allows: there
is no runtime surface that can show a mapping is unreachable.

- [ ] **Step 2: Run it to make sure it fails**

Run: `make test T=tests/unit/test_errors.py`
Expected: FAIL, listing the entries whose last raise site Tasks 5–8 removed

- [ ] **Step 3: Delete the entries that no longer have a raise site**

Edit `pipeline/shared/errors.py`, removing each `BUILTIN_STATUS` entry the test
named, and update the comment above it to say what the remaining entries are for.

- [ ] **Step 4: Add the checker to `make lint`**

In `Makefile`, immediately before the `no undefined names` line (~line 100):

```makefile
	@$(PY) tools/check_failures.py pipeline \
	  || { printf '  \033[31munnamed failures on a user-reachable path\033[0m\n'; exit 1; }
	@printf '  \033[32mevery user-reachable failure named\033[0m\n'
```

- [ ] **Step 5: Verify the ratchet actually holds**

Introduce a violation on purpose, confirm the build goes red, then revert it:

```bash
printf '\n\ndef _ratchet_probe():\n    raise ValueError("x")\n' >> pipeline/api/files.py
make check; echo "expected non-zero, got $?"
git checkout pipeline/api/files.py
make check && echo "green again"
```

- [ ] **Step 6: Run everything**

Run: `make check && make test`
Expected: all pass, and the new `every user-reachable failure named` line prints green

- [ ] **Step 7: Commit**

```bash
git add Makefile pipeline/shared/errors.py tests/unit/test_errors.py
git commit -m "make check now fails on an unnamed user-reachable failure

The count went 79 to 80 across a refactor because nothing was watching. It is
watched now, and verified by introducing a violation and seeing the build go
red. BUILTIN_STATUS lost the entries whose last raise site is gone; a test
fails if a surviving entry stops having one."
```

---

### Task 10: Record what was learned

**Files:**
- Modify: `docs/DECISIONS.md`
- Modify: `docs/REFACTOR.md` (§7, the row that says the count should reach zero)

- [ ] **Step 1: Correct the claim in `REFACTOR.md` §7**

Change the row so it states the checkable claim rather than the wrong one:

```markdown
| 79 builtin raises | count `ast.Raise` by exception name; the target is not zero — a CLI exit code and an abstract method are correctly builtins. The claim is "no builtin raise on a path reachable from a route handler", checked by `tools/check_failures.py` in `make check` |
```

- [ ] **Step 2: Add the finding to `docs/DECISIONS.md`**

Append under `## Operations`:

```markdown
### A taxonomy nobody checks goes backwards

`shared/errors.py` gave every failure a status and `server.py` a single dispatch
block, and the count of builtin raises then went from 79 to 80 across the next
refactor. Total raises grew 114 to 131: the new ones used the taxonomy and the
old ones were never converted, so the pattern looked installed and was half
installed. `BUILTIN_STATUS` — described in its own comment as scaffolding whose
entries should disappear — lost none.

The fix was not more conversion but a check that cannot be skipped, and getting
the claim right first. "Zero builtin raises" is the wrong target: `errors.py`
draws the rule that a ValueError reaching the server is a defect and a
PixelError is a message, so a CLI exit code and an abstract method must stay
builtins. Converting them would make every failure look like a user message,
which is the mirror of the bug.

The checkable claim is **no builtin raise on a path reachable from a route
handler**. `tools/check_failures.py` walks the call graph out from every
`@get`/`@post`/`@put` handler and fails `make check` on one. Name resolution is
by bare name and over-approximates deliberately: a false positive costs one
`# not-a-message: <reason>` marker, a false negative costs a 500 in front of
someone. The marker requires a reason, so the exemption is a claim someone can
read and disagree with rather than an omission nobody can see.
```

- [ ] **Step 3: Commit**

```bash
git add docs/DECISIONS.md docs/REFACTOR.md
git commit -m "Docs: the target was never zero, and why it went backwards

REFACTOR.md §7 said the builtin raise count should fall toward zero. Acting on
that would have converted CLI exit codes and abstract methods into user
messages, which is the mirror of the bug being fixed."
```

---

## Stage 1 done when

- `ComfyUI/.venv/bin/python tools/check_failures.py pipeline` exits 0
- `make check` prints `every user-reachable failure named` and fails when a violation is introduced
- every surviving builtin on a reachable path carries a marker with a reason
- `BUILTIN_STATUS` contains only entries that still have a raise site, enforced by a test
- `make test` is green

## What stages 2 and 3 need from this one

Nothing structural — the spec's §6 says each stage stands alone. But stage 1
settles what a route may *raise*, which is what makes stage 2's `check()` policy
(a config save rejecting an out-of-range value with `Invalid`) land in a codebase
where that already means something. Write the stage 2 plan after this one is
green, when the checker has shown which modules were actually reachable.
