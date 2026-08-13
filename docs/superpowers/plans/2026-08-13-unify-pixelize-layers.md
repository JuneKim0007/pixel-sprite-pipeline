# Unify pixelize() and the Layer System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the two orchestrations over the pixel primitives into one, so the pipeline's palette stage and the definitive editor run the same ordered layer stack — and a style sheet can define that order.

**Architecture:** `definitive/builtin.py` layers already express everything `pixelize()` does except three things (the `clipped` reducer, `clip_tolerance`, and loading a palette from an arbitrary path). Close those three gaps, add a `recipe.classic()` builder that returns the canonical 5-layer stack, then make both `pixelize()` and `PaletteStage` call `apply_stack` through it. `PaletteStage` then accepts a `palette.stack` list from config, which style sheets can set because `styles.expand` recurses into lists and `deep_merge` replaces them wholesale.

**Tech Stack:** Python 3, numpy, Pillow, PyYAML. No new dependencies.

**Spec:** This plan is the spec. It derives from the audit in this session; the three source-of-truth files are `pipeline/definitive/pixelize.py`, `pipeline/definitive/builtin.py`, `pipeline/stages/palette.py`.

## Global Constraints

- **Comments and docstrings: 1–2 lines maximum.** Owner's instruction, applies to every file touched.
- **`make check` before claiming anything works.** It runs ruff for undefined names first; that defect class raises at runtime six GPU-minutes into a stage.
- **`make test` must stay at 62 passed, 0 failed** (plus whatever this plan adds).
- **Test interpreter is `ComfyUI/.venv/bin/python`**, not system `python3` — system Python lacks `ruamel`. Always run tests via `make test`.
- **Measure before asserting.** Every non-obvious claim in a comment is followed by the measurement that produced it.
- Decided by the owner during planning: `palette.dither` becomes live (option 1); `palette.stack` is orderable from config and style sheets (option 3); the `pixelize.py` CLI is kept and rewired.

## Behaviour Changes This Plan Makes (accepted by the owner)

| Change | Who it affects | Today | After |
|---|---|---|---|
| `palette.dither` | pipeline configs | silently ignored (only read in the `colours > 0` branch the stage never takes) | median-cuts to palette size, then snaps |
| `pixelize.py -c/--colours` | CLI only | median cut | k-means in the matcher's own space, via the palette layer's `generate` |
| `palette.stack` | pipeline configs + style sheets | does not exist | ordered layer list, overrides the fixed recipe |
| `curves`, palette `fit`/`fit_strength` | pipeline configs | unavailable | available, defaulting to identity/off |
| `clipped` reducer | editor | not selectable | selectable |

No config in `configs/` or `styles/` currently sets `dither`, `phase`, or `clip_tolerance`, so existing runs are unaffected by all of the above.

---

### Task 1: Lock current output with a golden test

Nothing in the suite covers `pixelize()`'s pixels. Every later task is a refactor that must not change them, so the goldens come first.

**Files:**
- Create: `tests/golden/make_goldens.py`
- Create: `tests/golden/*.png` (generated, committed)
- Modify: `tests/test_api.py` (add section + two checks)

**Interfaces:**
- Produces: `_pixelize_goldens()` test function; `tests/golden/synthetic.png` input; `tests/golden/expect_*.png` outputs.

- [ ] **Step 1: Write the golden generator**

`tests/golden/make_goldens.py`:

```python
#!/usr/bin/env python3
"""Regenerate the pixelize goldens. Run only when a change is intended."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from pipeline.definitive.pixelize import pixelize, save_palette

CASES = {
    "fixed": dict(factor=8, reduce="median", colours=0, dither=False,
                  match="weighted", alpha_tol=14, upscale=1, phase=None,
                  tolerance=32.0),
    "clipped": dict(factor=8, reduce="clipped", colours=0, dither=False,
                    match="luma", alpha_tol=14, upscale=2, phase=(3, 5),
                    tolerance=18.0),
}
PALETTE = [(0, 0, 0), (60, 40, 90), (140, 70, 60), (220, 180, 120),
           (255, 255, 255), (40, 120, 160)]


def synthetic() -> np.ndarray:
    """A deterministic 256x256 with a flat backdrop, a ramp and hard edges."""
    rng = np.random.default_rng(7)
    arr = np.full((256, 256, 3), 200, dtype=np.uint8)
    ramp = np.linspace(0, 255, 160, dtype=np.uint8)
    arr[48:208, 48:208] = ramp[None, :, None]
    arr[96:160, 96:160] = (180, 40, 40)
    arr[110:130, 110:130] = (20, 20, 80)
    # Light noise so the block reducers actually differ from one another.
    noise = rng.integers(-6, 7, arr.shape, dtype=np.int16)
    return np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def main() -> int:
    HERE.mkdir(parents=True, exist_ok=True)
    src = HERE / "synthetic.png"
    Image.fromarray(synthetic()).save(src)
    save_palette(PALETTE, HERE / "palette.hex", note="golden fixture")
    for name, kw in CASES.items():
        pixelize(src, HERE / f"expect_{name}.png", palette=PALETTE,
                 verbose=False, key=None, **kw)
    print(f"wrote {len(CASES)} golden(s) to {HERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Generate the goldens against the CURRENT implementation**

Run: `ComfyUI/.venv/bin/python tests/golden/make_goldens.py`
Expected: `wrote 2 golden(s) to .../tests/golden`, and `synthetic.png`, `palette.hex`, `expect_fixed.png`, `expect_clipped.png` exist.

- [ ] **Step 3: Add the test that asserts against them**

In `tests/test_api.py`, add above `def _rig_cached() -> None:`:

```python
def _pixelize_goldens() -> None:
    """Pixel output must survive the move onto layers, byte for byte."""
    import tempfile

    from pipeline.definitive.pixelize import load_palette, pixelize
    from tests.golden.make_goldens import CASES

    here = ROOT / "tests" / "golden"
    palette = load_palette(here / "palette.hex")
    with tempfile.TemporaryDirectory() as tmp:
        for name, kw in CASES.items():
            got = Path(tmp) / f"{name}.png"
            pixelize(here / "synthetic.png", got, palette=palette,
                     verbose=False, key=None, **kw)
            _assert(got.read_bytes() == (here / f"expect_{name}.png").read_bytes(),
                    f"pixelize '{name}' no longer matches its golden")
```

- [ ] **Step 4: Register the checks**

In `test_pipeline()`, after the `rig editor` block:

```python
    print("\npixel output")
    check("pixelize matches its goldens", _pixelize_goldens)
```

- [ ] **Step 5: Run and verify it passes**

Run: `make test`
Expected: `pixelize matches its goldens` is `ok`; total is `63 passed, 0 failed`.

- [ ] **Step 6: Commit**

```bash
git add tests/golden tests/test_api.py
git commit -m "test: pin pixelize output with goldens before unifying it"
```

---

### Task 2: Grid layer gains the clipped reducer and its tolerance

`reduce_blocks` supports five modes; `REDUCERS` exposes four. `clip_tolerance` is the stage's only way to tune `clipped`, and the layer drops it, so the layer cannot express what the stage does.

**Files:**
- Modify: `pipeline/definitive/builtin.py:9-14` (REDUCERS), `:62-76` (`_grid_prepare`), `:79-116` (grid layer + `_grid`)
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: grid layer config accepts `clip_tolerance: float` (default `32.0`) and `reduce: "clipped"`.

- [ ] **Step 1: Write the failing test**

Add above `def _rig_cached() -> None:`:

```python
def _grid_layer_full() -> None:
    """The grid layer must reach every reducer, and pass clip_tolerance on."""
    import numpy as np

    from pipeline.definitive.layers import get
    from pipeline.definitive.run import apply_stack

    modes = {o[0] for o in __import__(
        "pipeline.definitive.builtin", fromlist=["REDUCERS"]).REDUCERS}
    _assert("clipped" in modes, "the clipped reducer is not offered")

    img = np.random.default_rng(3).integers(0, 255, (64, 64, 3), dtype=np.uint8)
    out = {}
    for tol in (4.0, 96.0):
        stack = [{"layer": "grid", "id": "grid0", "enabled": True,
                  "config": {**get("grid").defaults(),
                             "factor": 8, "reduce": "clipped",
                             "clip_tolerance": tol}}]
        out[tol], _ = apply_stack(img, stack, use_cache=False)
    _assert(not np.array_equal(out[4.0], out[96.0]),
            "clip_tolerance made no difference to the grid layer")
```

Register it in `test_pipeline()` under the `pixel output` section:

```python
    check("the grid layer reaches every reducer", _grid_layer_full)
```

- [ ] **Step 2: Run to verify it fails**

Run: `make test`
Expected: FAIL, `the clipped reducer is not offered`.

- [ ] **Step 3: Add the reducer and the field**

In `builtin.py`, extend `REDUCERS` (keep the measured note on median):

```python
REDUCERS = [
    ("median", "Median, robust to a stray bright pixel"),
    ("salient", "Salient, keeps the extreme where a block has contrast"),
    ("clipped", "Clipped, drops outliers then re-averages"),
    ("mode", "Mode, the most common exact colour"),
    ("mean", "Mean, smooth and most likely to invent a colour"),
]
```

Add to the grid layer's `fields`, after the `reduce` field:

```python
        Field("clip_tolerance", "Outlier distance", "float", min=1.0, max=128.0,
              step=1.0, default=32.0, when={"reduce": "clipped"},
              help="RGB distance from the block mean beyond which a pixel is "
                   "dropped before re-averaging."),
```

Change `_grid`'s last line to pass it through:

```python
    return px.reduce_blocks(img, prep["factor"], ox, oy,
                            cfg.get("reduce", "median"),
                            float(cfg.get("clip_tolerance", 32.0)))
```

- [ ] **Step 4: Run to verify it passes**

Run: `make test`
Expected: `the grid layer reaches every reducer` is `ok`, goldens still `ok`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/definitive/builtin.py tests/test_api.py
git commit -m "feat: grid layer offers the clipped reducer and its tolerance"
```

---

### Task 3: Only measure the block size when it is not given

`_grid_prepare` calls `training.estimate_block_size(img)` unconditionally, purely to record `measured_block`. The pipeline always supplies `factor`, so this is a per-frame measurement whose result is discarded.

**Files:**
- Modify: `pipeline/definitive/builtin.py:62-76`
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces: `prep["measured_block"]` is `None` when `factor` was given explicitly.

- [ ] **Step 1: Write the failing test**

Add above `def _rig_cached() -> None:`:

```python
def _grid_skips_measuring() -> None:
    """An explicit factor must not pay for a measurement it then ignores."""
    import numpy as np

    from pipeline.definitive import builtin
    from pipeline.looks import training

    calls = []
    original = training.estimate_block_size
    training.estimate_block_size = lambda img: (calls.append(1), original(img))[1]
    try:
        img = np.zeros((64, 64, 3), dtype=np.uint8)
        builtin._grid_prepare(img, {"factor": 8, "phase": "auto"})
        _assert(not calls, "measured the block size despite an explicit factor")
        builtin._grid_prepare(img, {"factor": 0, "phase": "auto"})
        _assert(len(calls) == 1, "did not measure when the factor was 0")
    finally:
        training.estimate_block_size = original
```

Register under `pixel output`:

```python
    check("an explicit block size skips the measurement", _grid_skips_measuring)
```

- [ ] **Step 2: Run to verify it fails**

Run: `make test`
Expected: FAIL, `measured the block size despite an explicit factor`.

- [ ] **Step 3: Make the measurement lazy**

Replace `_grid_prepare`'s first three lines:

```python
def _grid_prepare(img, cfg) -> dict:
    # Measured only when it will be used; the pipeline always gives a factor.
    given = int(cfg.get("factor") or 0)
    if given:
        measured, factor = None, given
    else:
        from ..looks import training

        measured = training.estimate_block_size(img)
        factor = max(1, int(round(measured)))
    factor = max(1, min(factor, min(img.shape[:2]) // 2 or 1))
```

The rest of the function is unchanged.

- [ ] **Step 4: Run to verify it passes**

Run: `make test`
Expected: all `ok`, goldens unchanged.

- [ ] **Step 5: Commit**

```bash
git add pipeline/definitive/builtin.py tests/test_api.py
git commit -m "perf: only measure the block size when the factor is not given"
```

---

### Task 4: The palette layer accepts a path, not only a registry key

The stage resolves its palette three ways (extract / file / llm) and writes the result to `<stage_dir>/palette.hex`. For the stage to hand that to the layer, `file` must accept a path. `PaletteStage._resolve_file` already accepts "registry key or plain path"; mirror it.

**Files:**
- Modify: `pipeline/definitive/builtin.py:122-143` (`_palette_prepare`), `:192-219` (`_palette`)
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces: palette layer config `{"source": "file", "file": "<registry key or path>"}` resolves either.

- [ ] **Step 1: Write the failing test**

Add above `def _rig_cached() -> None:`:

```python
def _palette_layer_takes_a_path() -> None:
    """The stage hands the layer a written file, not a registry name."""
    import numpy as np

    from pipeline.definitive.layers import get
    from pipeline.definitive.run import apply_stack

    here = ROOT / "tests" / "golden"
    img = np.random.default_rng(5).integers(0, 255, (32, 32, 3), dtype=np.uint8)
    stack = [{"layer": "palette", "id": "palette0", "enabled": True,
              "config": {**get("palette").defaults(),
                         "source": "file", "file": str(here / "palette.hex")}}]
    out, facts = apply_stack(img, stack, root=ROOT, use_cache=False)
    errors = [r.get("error") for r in facts["layers"] if r.get("error")]
    _assert(not errors, f"palette layer errored: {errors}")
    _assert(facts.get("palette_size") == 6, "the palette file was not applied")
```

Register under `pixel output`:

```python
    check("the palette layer accepts a path", _palette_layer_takes_a_path)
```

- [ ] **Step 2: Run to verify it fails**

Run: `make test`
Expected: FAIL, `palette layer errored: [...NotFound...]`.

- [ ] **Step 3: Resolve a path before falling back to the registry**

In `_palette`, replace the `if prep.get("file"):` block:

```python
    if prep.get("file"):
        from pathlib import Path

        from ..looks.palettes import registry

        # A path when the caller wrote the file itself; a key otherwise.
        given = Path(prep["file"])
        found = given if given.is_file() else Path(
            registry(facts["root"]).get(prep["file"]).path)
        palette = px.load_palette(found)
```

- [ ] **Step 4: Run to verify it passes**

Run: `make test`
Expected: `the palette layer accepts a path` is `ok`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/definitive/builtin.py tests/test_api.py
git commit -m "feat: the palette layer resolves a path as well as a registry key"
```

---

### Task 5: `recipe.classic()`, and pixelize() delegates to it

One builder for the canonical order, used by both callers. This is where the second orchestrator dies.

**Files:**
- Create: `pipeline/definitive/recipe.py`
- Modify: `pipeline/definitive/pixelize.py:420-470` (`pixelize`)
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces:
  - `recipe.classic(*, factor, reduce, tolerance, phase, palette_file, colours, match, dither, fit, fit_strength, alpha_tol, key, upscale, curves=None) -> list[dict]`
  - `recipe.run_file(src: Path, dst: Path, stack: list[dict], *, root: Path | None = None, use_cache: bool = False) -> dict` — returns `facts`

- [ ] **Step 1: Write `recipe.py`**

```python
"""One builder for the canonical layer order, shared by the CLI and the stage."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .layers import get
from .run import apply_stack


def _entry(key: str, enabled: bool, config: dict) -> dict:
    return {"layer": key, "id": f"{key}0", "enabled": enabled,
            "config": {**get(key).defaults(), **config}}


def classic(
    *,
    factor: int,
    reduce: str = "median",
    tolerance: float = 32.0,
    phase: tuple[int, int] | None = None,
    palette_file: str | None = None,
    colours: int = 0,
    match: str = "weighted",
    dither: bool = False,
    fit: bool = False,
    fit_strength: float = 1.0,
    alpha_tol: int | None = None,
    key: str = "",
    upscale: int = 1,
    curves: dict | None = None,
) -> list[dict]:
    """The fixed grid/palette/background/scale order, as a layer stack."""
    if palette_file:
        source = "file"
    elif colours > 0:
        source = "generate"
    else:
        source = "none"
    return [
        _entry("curves", bool(curves), curves or {}),
        _entry("grid", factor > 1, {
            "factor": factor, "reduce": reduce, "clip_tolerance": tolerance,
            "phase": "auto" if phase is None else "manual",
            "phase_x": 0 if phase is None else int(phase[0]),
            "phase_y": 0 if phase is None else int(phase[1]),
        }),
        _entry("palette", source != "none", {
            "source": source, "file": palette_file or "", "colours": colours,
            "match": match, "dither": dither, "fit": fit,
            "fit_strength": fit_strength,
        }),
        _entry("background", alpha_tol is not None, {
            "enabled": True, "tolerance": int(alpha_tol or 0), "colour": key,
        }),
        _entry("scale", upscale > 1, {"upscale": upscale}),
    ]


def run_file(src: Path, dst: Path, stack: list[dict], *,
             root: Path | None = None, use_cache: bool = False) -> dict:
    """Apply a stack to one file on disk. Returns the run's facts."""
    arr = np.asarray(Image.open(src).convert("RGB"))
    out, facts = apply_stack(arr, stack, root=root, use_cache=use_cache)
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(dst)
    return facts
```

- [ ] **Step 2: Run the goldens to confirm they still pass (nothing wired yet)**

Run: `make test`
Expected: `63 passed` plus this plan's additions, all `ok`.

- [ ] **Step 3: Make `pixelize()` delegate**

Replace the body of `pixelize()` in `pixelize.py`, keeping its signature exactly as it is today:

```python
    from . import recipe

    pal_file = None
    if palette:
        # The layer reads a file; write the caller's list to a temporary one.
        import tempfile

        tmp = Path(tempfile.mkdtemp()) / "palette.hex"
        save_palette(palette, tmp, note="in-memory")
        pal_file = str(tmp)

    stack = recipe.classic(
        factor=factor, reduce=reduce, tolerance=tolerance, phase=phase,
        palette_file=pal_file, colours=colours, match=match, dither=dither,
        alpha_tol=alpha_tol,
        key="" if key is None else "#%02x%02x%02x" % key,
        upscale=upscale,
    )
    facts = recipe.run_file(src, dst, stack)
    if verbose:
        for record in facts["layers"]:
            if record.get("error"):
                print(f"  {record['layer']}: {record['error']}")
    print(f"{src.name} -> {dst}  ({facts['after']['width']}x{facts['after']['height']})")
```

- [ ] **Step 4: Run the goldens**

Run: `make test`
Expected: `pixelize matches its goldens` is `ok`.

If it fails, diff the two paths before changing the goldens — the goldens are the contract. Likely culprits, in order: the `background` layer defaults `enabled` to `True` while `pixelize` keys only when `alpha_tol is not None`; the `scale` layer's default `upscale` is 4 while `pixelize`'s is 1; `dither` now reaching the palette layer. `classic()` pins all three explicitly, so a failure means one of them was missed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/definitive/recipe.py pipeline/definitive/pixelize.py
git commit -m "refactor: pixelize() runs the layer stack instead of its own order"
```

---

### Task 6: PaletteStage runs a stack

**Files:**
- Modify: `pipeline/stages/palette.py:27-37` (`_one_frame`), `:94-128` (the driver half of `run`)
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `recipe.classic`, `recipe.run_file`.
- Produces: `_one_frame(args: tuple[str, str, list[dict]]) -> str`.

- [ ] **Step 1: Write the failing test**

Add above `def _rig_cached() -> None:`:

```python
def _stage_stack_is_a_stack() -> None:
    """The stage must hand its pool a layer stack, not a 12-field tuple."""
    import inspect

    from pipeline.stages import palette as palette_stage

    source = inspect.getsource(palette_stage._one_frame)
    _assert("stack" in source, "_one_frame does not take a stack")
    _assert("upscale, dither, phase" not in source,
            "_one_frame still unpacks the old positional tuple")
```

Register under `pixel output`:

```python
    check("the palette stage drives a layer stack", _stage_stack_is_a_stack)
```

- [ ] **Step 2: Run to verify it fails**

Run: `make test`
Expected: FAIL, `_one_frame does not take a stack`.

- [ ] **Step 3: Replace the worker and the driver**

Replace `_one_frame`:

```python
def _one_frame(args: tuple) -> str:
    """Pool worker. The stack is plain dicts, so it pickles cheaply."""
    src, dst, stack = args
    recipe.run_file(Path(src), Path(dst), stack)
    return dst
```

Add `from ..definitive import recipe` to the imports, and drop the now-unused
`pixelize` name from the `..definitive.pixelize` import list.

Replace the driver half of `run()`, from the `jobs = [` list through
`done = [_one_frame(j) for j in jobs]`:

```python
        stack = cfg.get("stack") or recipe.classic(
            factor=factor, reduce=reduce, tolerance=tolerance,
            phase=shared_phase, palette_file=str(pal_path), match=match,
            dither=dither, fit=bool(opt(cfg, "fit", False)),
            fit_strength=float(opt(cfg, "fit_strength", 1.0)),
            alpha_tol=alpha_tol,
            key="" if key_colour is None else "#%02x%02x%02x" % key_colour,
            upscale=upscale, curves=opt(cfg, "curves", None),
        )
        for problem in check_order(stack):
            print(f"   warning: {problem}")

        jobs = [(str(src), str(outdir / f"{src.stem}_px.png"), stack)
                for src in frames]
        workers = min(opt(cfg, "workers", os.cpu_count() or 4), len(jobs)) or 1
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                done = list(pool.map(_one_frame, jobs))
        else:
            done = [_one_frame(j) for j in jobs]
```

Add `from ..definitive.layers import check_order` to the imports.

- [ ] **Step 4: Run to verify it passes**

Run: `make test`
Expected: all `ok`.

- [ ] **Step 5: Verify against a real run**

Run: `make run CONFIG=verify_short`
Expected: the palette stage reports `pixelized N frame(s)` and the PNGs in
`out/runs/<id>/*_palette/` are pixel art, not a smeared or blank image. Compare
one by eye against a previous run of the same config.

- [ ] **Step 6: Commit**

```bash
git add pipeline/stages/palette.py tests/test_api.py
git commit -m "refactor: the palette stage runs a layer stack"
```

---

### Task 7: `palette.stack` from config and style sheets

Task 6 already reads `cfg.get("stack")`. This task proves it survives style layering and documents it.

**Files:**
- Modify: `tests/test_api.py`
- Modify: `STYLES.md`, `CONFIGURING.md`

**Interfaces:**
- Consumes: `recipe.classic` output shape.

- [ ] **Step 1: Write the failing test**

Add above `def _rig_cached() -> None:`:

```python
def _style_sets_the_stack() -> None:
    """A style sheet must be able to reorder the palette stage's layers."""
    from pipeline.shared.settings import deep_merge
    from pipeline.looks.styles import expand

    stack = [{"layer": "grid", "id": "grid0", "enabled": True,
              "config": {"factor": 8}},
             {"layer": "curves", "id": "curves0", "enabled": True,
              "config": {"gamma": 1.2}}]
    survived = expand({"palette": {"stack": stack}}, {"mood": "grim"})
    _assert(survived["palette"]["stack"][0]["layer"] == "grid",
            "style expansion mangled the stack")

    # A list replaces rather than concatenates, so an override is a whole order.
    merged = deep_merge({"palette": {"stack": stack}},
                        {"palette": {"stack": stack[:1]}})
    _assert(len(merged["palette"]["stack"]) == 1,
            "overriding a stack appended to it instead of replacing it")
```

Register under `pixel output`:

```python
    check("a style sheet can order the palette stack", _style_sets_the_stack)
```

- [ ] **Step 2: Run to verify it passes**

Run: `make test`
Expected: `ok` — this asserts existing behaviour of `expand` and `deep_merge`, so it should pass immediately. If it fails, that is a real finding and Task 6's config path needs revisiting before continuing.

- [ ] **Step 3: Document it in `STYLES.md`**

Add under the settings section:

```markdown
### Ordering the pixel layers

A sheet can set `palette.stack` to the exact layer order the palette stage
runs, the same layers the editor offers:

```yaml
settings:
  palette:
    stack:
      - layer: curves
        config: {gamma: 1.15, saturation: 0.9}
      - layer: grid
        config: {factor: 8, reduce: median}
      - layer: palette
        config: {source: file, file: palettes/pico8.hex, match: luma}
      - layer: background
        config: {tolerance: 14}
```

A stack replaces rather than extends the one beneath it, so a sheet that sets
one owns the whole order. Omit `palette.stack` to keep the built-in order.
```

- [ ] **Step 4: Document the new keys in `CONFIGURING.md`**

Add `palette.stack`, `palette.fit`, `palette.fit_strength`, `palette.curves` to
the palette stage's key table, and note that `palette.dither` now takes effect.

- [ ] **Step 5: Run the full check**

Run: `make check && make test`
Expected: no undefined names, every config `ok`, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_api.py STYLES.md CONFIGURING.md
git commit -m "docs: style sheets can order the palette stage's layer stack"
```

---

### Task 8: Rewire the CLI

**Files:**
- Modify: `pipeline/definitive/pixelize.py:473-553` (`main`)

- [ ] **Step 1: Point `main()` at the recipe**

Replace the `for src in files:` loop body:

```python
    for src in files:
        dst = (a.outdir / f"{src.stem}_px.png") if a.outdir else \
            src.with_name(f"{src.stem}_px.png")
        if not a.quiet:
            print(src.name)
        stack = recipe.classic(
            factor=a.factor, reduce=a.reduce, tolerance=a.clip_tolerance,
            phase=phase, palette_file=str(a.palette) if a.palette else None,
            colours=a.colors, match=a.match, dither=a.dither,
            alpha_tol=a.alpha, upscale=a.upscale,
        )
        facts = recipe.run_file(src, dst, stack)
        if not a.quiet:
            print(f"  -> {dst} ({facts['after']['width']}x"
                  f"{facts['after']['height']}, {facts['after']['colours']} colours)")
```

Add `from . import recipe` at the top of `main()`, and update the `-c/--colours`
help to say `palette size by clustering; 0 disables (default: 32)`.

- [ ] **Step 2: Exercise the CLI both ways**

Run:
```bash
ComfyUI/.venv/bin/python pipeline/definitive/pixelize.py \
  tests/golden/synthetic.png -o /tmp/cli_check -f 8 -c 16
ComfyUI/.venv/bin/python pipeline/definitive/pixelize.py \
  tests/golden/synthetic.png -o /tmp/cli_check2 -f 8 \
  --palette tests/golden/palette.hex --alpha 14 -u 2
```
Expected: both write a `synthetic_px.png` and report a colour count — 16 or
fewer for the first, 6 or fewer for the second.

- [ ] **Step 3: Full check**

Run: `make check && make test`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add pipeline/definitive/pixelize.py
git commit -m "refactor: the pixelize CLI runs the same stack as everything else"
```

---

### Task 9: Delete what is now unreachable

**Files:**
- Modify: `pipeline/definitive/pixelize.py`

- [ ] **Step 1: Find what nothing calls any more**

Run:
```bash
for f in quantize_median_cut fit_to_palette apply_fixed_palette \
         background_to_alpha reduce_blocks find_phase generate_palette; do
  echo "$f: $(grep -rn "\b$f\b" pipeline/ tests/ tools/ | grep -v "def $f" | wc -l)"
done
```
Expected: every one still has callers — the layers use them. Nothing in this
list is a removal candidate; this step exists to prove that before deleting.

- [ ] **Step 2: Confirm the driver is the only casualty**

The only thing this plan supersedes is `pixelize()`'s ordering logic, which is
now `recipe.classic`. Per `AGENTS.md`, code is removed only when genuinely
superseded — same feature, done better elsewhere — which this is. `pixelize()`
itself stays as the CLI's entry point.

- [ ] **Step 3: Run the full suite one last time**

Run: `make check && make test`
Expected: no undefined names, all tests pass.

- [ ] **Step 4: Commit**

```bash
git commit --allow-empty -m "chore: confirm no orphans left by the pixelize unification"
```

---

## Self-Review

**Spec coverage:** Every row of the behaviour-change table maps to a task —
dither and the new palette keys land in Task 6, `palette.stack` in Tasks 6–7,
the CLI's `-c` change in Task 8, `clipped` in Task 2. The three gaps named in
the architecture note are Tasks 2, 3 (tolerance and measurement) and 4 (path).

**Placeholder scan:** No TBDs. Every code step carries the actual code. Task 5
Step 4 names the three specific likely failure causes rather than saying "debug
if it fails".

**Type consistency:** `recipe.classic` is called with the same keyword names in
Tasks 5, 6 and 8. `recipe.run_file(src, dst, stack)` returns `facts`, used as
`facts["after"]["width"]` in Tasks 5 and 8 and ignored in Task 6.
`_one_frame(args)` takes `(str, str, list[dict])` in both its definition and
its call site.

**Known risk:** Task 5 Step 4 is where this plan either holds or does not. If
the goldens shift, the cause is a default mismatch between `classic()` and
`pixelize()`'s old fixed order, not a deep problem — but do not regenerate the
goldens to make it green.
