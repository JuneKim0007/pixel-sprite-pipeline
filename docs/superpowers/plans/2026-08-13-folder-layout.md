# Folder Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the top level by lifecycle — `library/` authored, `out/` derived, `var/` operational — and give every asset library one discovery rule.

**Architecture:** One `pipeline/shared/paths.py` becomes the only place a data directory is named. It reads `paths` from `_global.yaml` and falls back to the new layout. The 14 hardcoded `root / "..."` sites are rewritten to call it. Runs stay a flat timestamped log; the export stage additionally files finished work under `out/exports/<name>/<kind>/`.

**Tech Stack:** Python 3, PyYAML. No new dependencies.

**Spec:** This plan is the spec. Owner decisions: full three-way split; exports keyed by asset name; runs stay run-centric.

## Global Constraints

- **No docstrings or comments unless a line is genuinely unobvious, and never more than 2 lines.** Owner's instruction, repeated. Default to none.
- **`make check` before claiming anything works** — ruff for undefined names runs first.
- **Tests run on `ComfyUI/.venv/bin/python`**, not system `python3` (no `ruamel` there). `make test` also runs the frontend suite, which another agent is actively changing; for backend-only use `ComfyUI/.venv/bin/python tests/test_api.py`.
- **Backend baseline is 66 passed, 0 failed.** The API portion needs `server.py` running on :8000.
- **Do not touch `web/`.** Another agent owns it this session.
- Never restart ComfyUI while a generation is in flight.

## Current State

14 hardcoded directory names:

| File:line | Names |
|---|---|
| `api/context.py:13` | `configs` |
| `api/poses.py:45` | `poses` |
| `generation/stage.py:158` | `training` |
| `generation/schema.py:758,760` | `poses`, `poses/generated` |
| `looks/palettes.py:65,89` | `palettes` (x2) |
| `orchestration/queue.py:68,175` | `queue`, `configs` |
| `stages/pose.py:270,272,297` | `poses` (x3) |
| `autopilot.py:52` | `configs` |
| `refs/references.py:212` | `out/runs` |
| `shared/settings.py:68` | `configs` |

Already configurable via `paths` in `_global.yaml`: `input_dir`, `output_dir`, `download_dir`.

Discovery rules disagree:

| Library | Pattern | Nesting |
|---|---|---|
| palettes | `**/*.hex` | full |
| props | `**/*.yaml` | full |
| styles | `*.yaml`, `*/style.yaml` | one level |
| poses | `*.json` (3 readers, only `schema.py` also reads `generated/`) | none |

## Target

```
library/
  configs/{_global.yaml, experiments/}
  styles/  palettes/  poses/  props/  refs/
out/
  runs/<run_id>/        unchanged
  exports/<name>/<kind>/
  scratch/
var/
  queue/  logs/  overnight/
```

---

### Task 1: One module that names every directory

**Files:**
- Create: `pipeline/shared/paths.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces:
  - `LAYOUT: dict[str, str]` — logical name -> default relative path
  - `resolve(root: Path, name: str, overrides: dict | None = None) -> Path` — absolute, created on demand
  - `legacy_for(name: str) -> str` — the pre-split relative path, for the migration check

`shared/` may not import any module. This file imports only `pathlib`, so the rule holds.

- [ ] **Step 1: Write the failing test**

Add above `def _route_table() -> None:` in `tests/test_api.py`:

```python
def _paths_module() -> None:
    from pipeline.shared import paths

    for name in ("configs", "styles", "palettes", "poses", "props", "refs",
                 "runs", "exports", "scratch", "queue", "logs", "overnight",
                 "training"):
        _assert(name in paths.LAYOUT, f"paths.LAYOUT has no '{name}'")

    got = paths.resolve(ROOT, "poses")
    _assert(got.is_absolute(), "resolve returned a relative path")
    _assert(got.parts[-2:] == ("library", "poses"), f"unexpected default: {got}")

    moved = paths.resolve(ROOT, "poses", {"poses": "somewhere/else"})
    _assert(moved.parts[-2:] == ("somewhere", "else"), "an override was ignored")
```

Register it in `test_pipeline()` under a new section, after the `stage defaults` block:

```python
    print("\nlayout")
    check("every data directory is named in one place", _paths_module)
```

- [ ] **Step 2: Run to verify it fails**

Run: `ComfyUI/.venv/bin/python tests/test_api.py`
Expected: `ERROR ... ModuleNotFoundError: No module named 'pipeline.shared.paths'`

- [ ] **Step 3: Write the module**

`pipeline/shared/paths.py`:

```python
from __future__ import annotations

from pathlib import Path

LAYOUT: dict[str, str] = {
    "configs": "library/configs",
    "experiments": "library/configs/experiments",
    "styles": "library/styles",
    "palettes": "library/palettes",
    "poses": "library/poses",
    "props": "library/props",
    "refs": "library/refs",
    "runs": "out/runs",
    "exports": "out/exports",
    "scratch": "out/scratch",
    "training": "out/training",
    "queue": "var/queue",
    "logs": "var/logs",
    "overnight": "var/overnight",
}

LEGACY: dict[str, str] = {
    "configs": "configs",
    "experiments": "configs",
    "styles": "styles",
    "palettes": "palettes",
    "poses": "poses",
    "props": "props",
    "refs": "inputs",
    "runs": "out/runs",
    "exports": "exports",
    "scratch": "out",
    "training": "training",
    "queue": "queue",
    "logs": "logs",
    "overnight": "overnight",
}


def legacy_for(name: str) -> str:
    return LEGACY[name]


def resolve(root: Path, name: str, overrides: dict | None = None) -> Path:
    raw = str((overrides or {}).get(name) or LAYOUT[name]).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path(root) / path
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
```

- [ ] **Step 4: Run to verify it passes**

Run: `ComfyUI/.venv/bin/python tests/test_api.py`
Expected: `every data directory is named in one place` is `ok`.

Also run `make check` — `shared/` must still depend on nothing, which
`_shared_has_no_module_deps` enforces.

- [ ] **Step 5: Commit**

```bash
git add pipeline/shared/paths.py tests/test_api.py
git commit -m "feat: one module names every data directory"
```

---

### Task 2: One discovery rule per library

Before anything moves, make discovery consistent — so the move cannot be blamed for a file going missing.

**Files:**
- Modify: `pipeline/looks/styles.py:148-150`, `pipeline/api/poses.py:44-47`, `pipeline/stages/pose.py:270-272`, `pipeline/generation/schema.py:756-761`
- Create: `pipeline/looks/poses.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `pipeline/looks/poses.py` with
  - `discover(root: Path) -> dict[str, Path]` — key is the path relative to the poses dir, without suffix, so `generated/run_6f` is a key
  - `load(root: Path, name: str) -> dict`

- [ ] **Step 1: Write the failing test**

```python
def _one_pose_library() -> None:
    import json
    import shutil

    from pipeline.looks import poses as pose_lib
    from pipeline.shared import paths

    base = paths.resolve(ROOT, "poses")
    nested = base / "generated"
    nested.mkdir(parents=True, exist_ok=True)
    probe = nested / "_layout_probe.json"
    probe.write_text(json.dumps({"space": "body", "frames": [{}]}))
    try:
        found = pose_lib.discover(ROOT)
        _assert("generated/_layout_probe" in found,
                f"a nested pose was not discovered: {sorted(found)[:5]}")
        _assert(pose_lib.load(ROOT, "generated/_layout_probe")["space"] == "body",
                "a nested pose could not be loaded by key")
    finally:
        probe.unlink()
        if not any(nested.iterdir()):
            shutil.rmtree(nested)
```

Register under `layout`:

```python
    check("the pose library has one reader", _one_pose_library)
```

- [ ] **Step 2: Run to verify it fails**

Expected: `ModuleNotFoundError: No module named 'pipeline.looks.poses'`

- [ ] **Step 3: Write the pose library**

`pipeline/looks/poses.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from ..shared.errors import NotFound
from ..shared import paths


def discover(root: Path) -> dict[str, Path]:
    base = paths.resolve(Path(root), "poses")
    return {str(p.relative_to(base).with_suffix("")): p
            for p in sorted(base.rglob("*.json"))}


def load(root: Path, name: str) -> dict:
    found = discover(root)
    if name not in found:
        raise NotFound("pose", name, available=found)
    return json.loads(found[name].read_text())
```

- [ ] **Step 4: Point the three readers at it**

In `pipeline/stages/pose.py`, replace the body of `_from_library` down to the
`json.loads` with:

```python
        from ..looks import poses as pose_lib

        pose_name = cfg["name"]
        data = pose_lib.load(ctx.root, pose_name)
```

Delete the now-dead `path`/`options`/`FileNotFoundError` block; `NotFound`
already carries the alternatives.

In `pipeline/api/poses.py`, replace the library listing:

```python
    from ..looks import poses as pose_lib

    return {"library": {k: json.loads(p.read_text())
                        for k, p in pose_lib.discover(ROOT).items()}}
```

In `pipeline/generation/schema.py`, replace lines 758-761 with:

```python
    from ..looks import poses as pose_lib

    poses = sorted(pose_lib.discover(root))
```

- [ ] **Step 5: Widen the style pattern**

In `pipeline/looks/styles.py:149`, change the patterns from
`["*.yaml", f"*/{SHEET}"]` to `["*.yaml", f"**/{SHEET}"]`.

- [ ] **Step 6: Run the suite**

Run: `make check && ComfyUI/.venv/bin/python tests/test_api.py`
Expected: `66 passed` plus this plan's additions, all `ok`.

- [ ] **Step 7: Commit**

```bash
git add pipeline/looks/poses.py pipeline/looks/styles.py pipeline/api/poses.py \
        pipeline/stages/pose.py pipeline/generation/schema.py tests/test_api.py
git commit -m "refactor: one discovery rule for every asset library"
```

---

### Task 3: Route every hardcoded path through paths.py

**Files:**
- Modify: `pipeline/api/context.py:13`, `pipeline/generation/stage.py:158`, `pipeline/looks/palettes.py:65,89`, `pipeline/orchestration/queue.py:68,175`, `pipeline/shared/settings.py:68`, `pipeline/refs/references.py:212`, `autopilot.py:52`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `paths.resolve`, `paths.LAYOUT`.

- [ ] **Step 1: Write the failing test**

```python
def _no_stray_data_dirs() -> None:
    import pathlib
    import re

    pattern = re.compile(
        r'(ROOT|root|ctx\.root|self\.root)\s*/\s*"'
        r'(poses|palettes|styles|props|configs|inputs|exports|queue|overnight|logs|training)"')
    offenders = []
    for f in sorted(pathlib.Path("pipeline").rglob("*.py")):
        if f.name == "paths.py":
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{f}:{i}")
    _assert(not offenders, f"data directories named outside paths.py: {offenders}")
```

Register under `layout`:

```python
    check("no module names a data directory itself", _no_stray_data_dirs)
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL listing roughly 10 sites.

- [ ] **Step 3: Rewrite each site**

`pipeline/shared/settings.py:68` — `settings` may import `paths` (both are in
`shared/`, and `paths` imports nothing):

```python
def global_path(root: Path) -> Path:
    return paths.resolve(root, "configs") / f"{GLOBAL_NAME}.yaml"
```

Add `from . import paths` to its imports.

`pipeline/api/context.py:13`:

```python
CONFIGS = paths.resolve(ROOT, "configs")
```

`pipeline/generation/stage.py:158`:

```python
        path = paths.resolve(self.root, "training") / kind
```

`pipeline/looks/palettes.py` — replace both `root / "palettes"` with
`paths.resolve(root, "palettes")`.

`pipeline/orchestration/queue.py:68`:

```python
        self.root = paths.resolve(root, "queue")
```

and `:175`:

```python
    cfg_path = paths.resolve(root, "configs") / f"{job.config}.yaml"
```

`autopilot.py:52` — the same `configs` change.

`pipeline/refs/references.py:212`:

```python
    base = Path(cfg.get("_runs_dir") or paths.resolve(root, "runs")) / run_id
```

- [ ] **Step 4: Point settings.DEFAULT_GLOBAL at the new layout**

In `pipeline/shared/settings.py`, update the `paths` block:

```python
    "paths": {
        "input_dir": "library/refs",
        "output_dir": "out/runs",
        "download_dir": "out/exports",
    },
```

- [ ] **Step 5: Run the suite**

Run: `make check && ComfyUI/.venv/bin/python tests/test_api.py`
Expected: all `ok`, including `no module names a data directory itself`.

- [ ] **Step 6: Commit**

```bash
git add -u && git commit -m "refactor: every data directory comes from paths.py"
```

---

### Task 4: The migration, run once and idempotent

**Files:**
- Create: `tools/migrate_layout.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `tools/migrate_layout.py`, runnable as `ComfyUI/.venv/bin/python tools/migrate_layout.py [--dry-run]`

- [ ] **Step 1: Write the migration**

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.shared import paths

SKIP = {"scratch"}


def moves() -> list[tuple[Path, Path]]:
    out = []
    for name, target in paths.LAYOUT.items():
        if name in SKIP:
            continue
        old = ROOT / paths.legacy_for(name)
        new = ROOT / target
        if old.resolve() == new.resolve() or not old.is_dir():
            continue
        out.append((old, new))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    planned = moves()
    if not planned:
        print("nothing to move; layout is already current")
        return 0

    for old, new in planned:
        print(f"{old.relative_to(ROOT)} -> {new.relative_to(ROOT)}")
        if a.dry_run:
            continue
        new.parent.mkdir(parents=True, exist_ok=True)
        if new.exists():
            for item in old.iterdir():
                dst = new / item.name
                if dst.exists():
                    print(f"  skip {item.name}: already at the destination")
                    continue
                shutil.move(str(item), str(dst))
            if not any(old.iterdir()):
                old.rmdir()
        else:
            shutil.move(str(old), str(new))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Dry run and read the output**

Run: `ComfyUI/.venv/bin/python tools/migrate_layout.py --dry-run`
Expected: one line per directory that exists. `configs -> library/configs`,
`inputs -> library/refs`, `queue -> var/queue`, and so on. `out/runs` must NOT
appear — it does not move.

- [ ] **Step 3: Migrate for real**

Run: `ComfyUI/.venv/bin/python tools/migrate_layout.py`
Then: `ls library/ out/ var/`

- [ ] **Step 4: Move the experiment configs**

The twelve `_`-prefixed configs are A/B fixtures, not pipelines:

```bash
mkdir -p library/configs/experiments
git mv library/configs/_ab_illu.yaml library/configs/_ab_sdxl.yaml \
       library/configs/_ap_probe.yaml library/configs/_cond_off.yaml \
       library/configs/_cond_on.yaml library/configs/_cond_poseonly.yaml \
       library/configs/_illu_cfg40.yaml library/configs/_illu_cfg50.yaml \
       library/configs/_illu_cfg60.yaml library/configs/_memtest_batch.yaml \
       library/configs/_memtest_seq.yaml library/configs/experiments/
```

`_global.yaml` stays at `library/configs/_global.yaml`.

- [ ] **Step 5: Sweep the loose derived files into scratch**

```bash
mkdir -p out/scratch
git mv out/px out/px_db32 out/px_pico out/px_raw out/batch out/gpuonly out/scratch/ 2>/dev/null || \
  mv out/px out/px_db32 out/px_pico out/px_raw out/batch out/gpuonly out/scratch/
mv out/sprite_*.png out/scratch/ 2>/dev/null || true
mv out/autopilot.log var/logs/ 2>/dev/null || true
```

- [ ] **Step 6: Rewrite .gitignore for the three lifecycles**

```
# Vendored ComfyUI checkout and its multi-gigabyte weights.
ComfyUI/

# Derived. Runs are reproducible from their manifest.
out/

# Operational state, not source. The queue examples are.
var/
!var/queue/examples/

# Reference art: source images, not code, and not ours to redistribute.
library/refs/

# Subagent reports — regenerated on demand.
audit/

__pycache__/
*.py[cod]
.DS_Store
*__mirror.png
```

Note `overnight/README.md` was tracked before; it now lives at
`var/overnight/README.md` and is ignored. Move it to `docs/OVERNIGHT.md` or add
`!var/overnight/README.md`.

- [ ] **Step 7: Verify the app still works end to end**

Run:
```bash
make check
ComfyUI/.venv/bin/python tests/test_api.py
```
Then start the server and load a config:
```bash
ComfyUI/.venv/bin/python server.py &
curl -s http://127.0.0.1:8000/api/configs | head -c 300
curl -s http://127.0.0.1:8000/api/poses | head -c 200
```
Expected: configs and poses both list. An empty list means discovery did not
follow the move — fix before continuing.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: split the top level into library/, out/ and var/"
```

---

### Task 5: Exports keyed by asset name

**Files:**
- Modify: `pipeline/stages/export.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `ExportStage.DEFAULTS` gains `{"publish": True}`; the stage writes
  `out/exports/<name>/<kind>/sheet.png` in addition to its run-dir output.

`<name>` is `ctx.config.get("name")` falling back to `ctx.run_id`; `<kind>` is
the pipeline module (`character_sheet`, `animation`), falling back to `sheet`.

- [ ] **Step 1: Write the failing test**

```python
def _exports_are_keyed_by_asset() -> None:
    import inspect

    from pipeline.stages import export

    _assert("publish" in export.ExportStage.DEFAULTS,
            "export has no publish setting")
    src = inspect.getsource(export)
    _assert('"exports"' in src, "the export stage never resolves the exports dir")
```

Register under `layout`:

```python
    check("exports are filed under the asset name", _exports_are_keyed_by_asset)
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, `export has no publish setting`.

- [ ] **Step 3: Publish alongside the run output**

In `pipeline/stages/export.py`, change `DEFAULTS` to
`{"scale": 1, "publish": True}` and add, immediately before the stage's
`return`:

```python
        if cfg["publish"]:
            name = ctx.config.get("name") or ctx.run_id
            kind = ctx.config.get("module") or "sheet"
            published = paths.resolve(ctx.root, "exports") / name / kind
            published.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, published / "sheet.png")
```

Add `import shutil` and `from ..shared import paths` to the imports.

- [ ] **Step 4: Run the suite**

Run: `make check && ComfyUI/.venv/bin/python tests/test_api.py`
Expected: all `ok`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/stages/export.py tests/test_api.py
git commit -m "feat: finished sheets are published under the asset name"
```

---

### Task 6: Documentation

**Files:**
- Modify: `AGENTS.md`, `CONFIGURING.md`, `README.md`

- [ ] **Step 1: Update the layout section in AGENTS.md**

Replace the `## Layout` directory listing with the three-way split, and add:

```
    library/   authored and versioned: configs, styles, palettes, poses,
               props, refs. Everything here is yours and is backed up.
    out/       derived and gitignored: runs/, exports/, scratch/.
    var/       operational and gitignored: queue/, logs/, overnight/.

Every data directory is named in pipeline/shared/paths.py and nowhere else;
a test enforces it.
```

- [ ] **Step 2: Update CONFIGURING.md**

Document the `paths` block's new defaults (`library/refs`, `out/runs`,
`out/exports`) and the new `export.publish` key.

- [ ] **Step 3: Update README.md**

Fix any path in the quick-start that points at `inputs/` or `configs/`.

- [ ] **Step 4: Final check**

Run: `make check && ComfyUI/.venv/bin/python tests/test_api.py`

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md CONFIGURING.md README.md
git commit -m "docs: the three-way layout, and where directories are named"
```

---

## Self-Review

**Spec coverage:** Three-way split is Tasks 1, 3, 4. Asset-keyed exports is
Task 5. Discovery consistency — the defect that motivated this — is Task 2 and
deliberately lands *before* anything moves, so a missing file can never be
ambiguous between "moved wrong" and "was never discoverable".

**Placeholder scan:** No TBDs. Every step carries its code or its exact
command. Task 4 Step 7 names what a failure looks like rather than saying
"verify it works".

**Type consistency:** `paths.resolve(root, name, overrides=None) -> Path` is
called with the same signature in Tasks 3, 4 and 5. `pose_lib.discover(root) ->
dict[str, Path]` is consumed as a mapping in all three readers in Task 2.
`legacy_for` is used only by the migration.

**Known risks:**
1. Task 4 moves real data. Run `--dry-run` first and read every line. `out/runs`
   must not appear in the plan output.
2. `.gitignore` currently tracks `overnight/README.md`; the new `var/` rule
   would ignore it. Task 4 Step 6 calls this out — do not skip it.
3. `queue/examples/` is tracked and must survive as `var/queue/examples/`; the
   negation rule is in the new `.gitignore`.
4. Existing run directories contain a `config.yaml` whose `paths` block may name
   old locations. Resume reads that snapshot, so an old run resumes against old
   paths — which is correct, and why `out/runs` does not move.
