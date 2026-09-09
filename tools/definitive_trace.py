#!/usr/bin/env python3
"""Minimal diagnostic harness for the Definitive Layer.

Runs ONE image through ONE layer at a time, logging resource state before and
after every operation. It does NOT run the sprite pipeline, does not touch
ComfyUI, does not start the server, and never uses a process pool.

The point is to find where resource usage CHANGES, not to make anything faster.
Nothing here is optimised and nothing in the pipeline is modified.

    python3 definitive_trace.py --list
    python3 definitive_trace.py --layer grid --size 64
    python3 definitive_trace.py --all --size 64 --json trace.json

If the machine dies mid-run, DO NOT rerun. The log is flushed and fsynced after
every line, so the last line in --log names the operation that was in flight.
Read the "IF THE MACHINE DIES" section printed at startup.
"""

from __future__ import annotations

import os

_THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
for _v in _THREAD_VARS:
    os.environ[_v] = "1"
os.environ["PIXEL_NO_POOL"] = "1"

import argparse                                                    # noqa: E402
import gc                                                          # noqa: E402
import json                                                        # noqa: E402
import platform                                                    # noqa: E402
import resource                                                    # noqa: E402
import signal                                                      # noqa: E402
import subprocess                                                  # noqa: E402
import sys                                                         # noqa: E402
import threading                                                   # noqa: E402
import time                                                        # noqa: E402
import traceback                                                   # noqa: E402
import tracemalloc                                                 # noqa: E402
from pathlib import Path                                           # noqa: E402

PIPELINE_ROOT = Path("/Users/personal_jk/pixel")
if not (PIPELINE_ROOT / "pipeline" / "definitive").exists():
    PIPELINE_ROOT = Path.cwd()
sys.path.insert(0, str(PIPELINE_ROOT))

import numpy as np                                                 # noqa: E402
from PIL import Image                                              # noqa: E402

MAX_EDGE = 512
MAX_PIXELS = 512 * 512
MAX_PREDICTED_GB = 1.0
MAX_LAYER_SECONDS = 120
MAX_TOTAL_SECONDS = 600
BYTES_PER_PX_PER_COLOUR = 16

_T0 = time.perf_counter()


def _stamp() -> str:
    dt = time.perf_counter() - _T0
    return f"[{int(dt // 60):02d}:{dt % 60:05.2f}]"


def rss_bytes() -> int:
    """Resident set size of THIS process. ru_maxrss is bytes on macOS."""
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def cpu_times() -> tuple[float, float]:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime, r.ru_stime


def thread_count() -> tuple[int, int]:
    """(python-level threads, OS-level threads). They differ when a native
    library spawns its own - which is the case this is here to catch."""
    py = threading.active_count()
    os_threads = -1
    try:
        out = subprocess.run(["ps", "-M", "-p", str(os.getpid())],
                             capture_output=True, text=True, timeout=5)
        os_threads = max(0, len(out.stdout.strip().splitlines()) - 1)
    except (OSError, subprocess.SubprocessError):
        pass
    return py, os_threads


def subprocess_count() -> int:
    """Direct children. Stays 0 for the whole run; a pool would show here."""
    try:
        out = subprocess.run(["pgrep", "-P", str(os.getpid())],
                             capture_output=True, text=True, timeout=5)
        return len([x for x in out.stdout.split() if x.strip()])
    except (OSError, subprocess.SubprocessError):
        return -1


def memory_pressure() -> int:
    """System pressure: 1 normal, 2 warn, 4 critical. -1 means COULD NOT READ,
    which is deliberately distinct from 1 - the shipped guard conflates them."""
    try:
        out = subprocess.run(
            ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
            capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1


def system_free_mb() -> float:
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return -1.0
    page, free = 16384, 0
    for line in out.stdout.splitlines():
        if "page size of" in line:
            for tok in line.split():
                if tok.isdigit():
                    page = int(tok)
        if line.startswith(("Pages free", "Pages speculative")):
            v = line.split(":")[1].strip().rstrip(".")
            if v.isdigit():
                free += int(v)
    return free * page / 1e6


def retained_arrays() -> tuple[int, float]:
    """What the interpreter is still holding.

    Traced bytes, not an object count: numeric-dtype ndarrays are invisible to
    gc.get_objects(), so a gc-based tally reads zero while megabytes are live.
    The integer is live allocation blocks. See docs/DIAGNOSTIC-HARNESS.md.
    """
    if not tracemalloc.is_tracing():
        return -1, -1.0
    current, _peak = tracemalloc.get_traced_memory()
    blocks = len(tracemalloc.take_snapshot().statistics("filename"))
    return blocks, current / 1e6


def retained_images() -> int:
    n = 0
    for obj in gc.get_objects():
        try:
            if isinstance(obj, Image.Image):
                n += 1
        except ReferenceError:
            continue
    return n


def sample(label: str) -> dict:
    py_t, os_t = thread_count()
    n_arr, mb_arr = retained_arrays()
    u, s = cpu_times()
    return {
        "t": round(time.perf_counter() - _T0, 4),
        "label": label,
        "rss_mb": round(rss_bytes() / 1e6, 1),
        "cpu_user_s": round(u, 3),
        "cpu_sys_s": round(s, 3),
        "threads_py": py_t,
        "threads_os": os_t,
        "subprocesses": subprocess_count(),
        "pressure": memory_pressure(),
        "sys_free_mb": round(system_free_mb(), 1),
        "traced_blocks": n_arr,
        "traced_mb": round(mb_arr, 2),
        "pil_images_retained": retained_images(),
    }


class Log:
    def __init__(self, path: Path | None):
        self.fh = open(path, "a", buffering=1) if path else None
        self.rows: list[dict] = []

    def line(self, text: str) -> None:
        print(text, flush=True)
        if self.fh:
            self.fh.write(text + "\n")
            self.fh.flush()
            os.fsync(self.fh.fileno())

    def event(self, label: str, extra: str = "") -> dict:
        s = sample(label)
        self.rows.append(s)
        self.line(f"{_stamp()} {label}{(' ' + extra) if extra else ''}"
                  f"  RSS={s['rss_mb']}MB  cpu={s['cpu_user_s']}u/{s['cpu_sys_s']}s"
                  f"  thr={s['threads_py']}py/{s['threads_os']}os"
                  f"  subp={s['subprocesses']}  pressure={s['pressure']}"
                  f"  free={s['sys_free_mb']}MB"
                  f"  traced={s['traced_mb']}MB/{s['traced_blocks']}blk"
                  f"  pil={s['pil_images_retained']}")
        return s

    def close(self) -> None:
        if self.fh:
            self.fh.close()


def describe(arr) -> str:
    """One intermediate's real dimensions, mode, dtype and bytes."""
    if arr is None:
        return "None"
    if isinstance(arr, np.ndarray):
        h, w = arr.shape[0], arr.shape[1]
        c = arr.shape[2] if arr.ndim > 2 else 1
        mode = {1: "L", 3: "RGB", 4: "RGBA"}.get(c, f"{c}ch")
        return (f"{w}x{h} {mode} {arr.dtype} "
                f"= {arr.nbytes / 1e6:.2f}MB ({arr.nbytes:,}B)")
    return type(arr).__name__


class Alarm:
    def __init__(self, seconds: int, what: str):
        self.seconds, self.what = seconds, what

    def __enter__(self):
        def fire(signum, frame):
            raise TimeoutError(f"{self.what} exceeded {self.seconds}s")
        self.prev = signal.signal(signal.SIGALRM, fire)
        signal.alarm(self.seconds)
        return self

    def __exit__(self, *exc):
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self.prev)
        return False


def load_input(path: Path, size: int, log: Log) -> np.ndarray:
    """One real image, centre-cropped to `size`.

    Cropped and never resized: resampling rewrites the pixel lattice, and the
    lattice is exactly what the grid layer measures. A resized sprite would
    report a block size that the original does not have.
    """
    with Image.open(path) as probe:
        w, h = probe.size
        log.line(f"{_stamp()} source file: {path.name} {w}x{h} mode={probe.mode}")
        if min(w, h) < size:
            raise SystemExit(f"REFUSED: {path.name} is {w}x{h}, smaller than "
                             f"the requested {size}px crop.")
        left, top = (w - size) // 2, (h - size) // 2
        cropped = probe.convert("RGBA").crop((left, top, left + size, top + size))
    img = np.asarray(cropped, dtype=np.uint8).copy()
    del cropped
    gc.collect()
    log.line(f"{_stamp()} input loaded: {describe(img)} "
             f"(centre crop of {path.name})")
    return img


def make_input(size: int, log: Log) -> np.ndarray:
    """One image. Synthetic and deterministic, so a rerun is comparable.

    Anti-aliased content on purpose: it is the worst case for the palette
    layer, because nearly every pixel is a distinct colour.
    """
    if size > MAX_EDGE or size * size > MAX_PIXELS:
        raise SystemExit(f"REFUSED: {size}x{size} exceeds the harness limit "
                         f"({MAX_EDGE} per side / {MAX_PIXELS:,} px).")
    rng = np.random.default_rng(0)
    ramp = np.linspace(0, 255, size, dtype=np.float32)
    field = (ramp[None, :] * 0.6 + ramp[:, None] * 0.4)[..., None] * np.array([1.0, 0.7, 0.4])
    rgb = np.clip(field + rng.normal(0, 8, (size, size, 3)), 0, 255).astype(np.uint8)
    alpha = np.full((size, size, 1), 255, dtype=np.uint8)
    img = np.concatenate([rgb, alpha], axis=2)

    del ramp, field, rgb, alpha
    gc.collect()
    log.line(f"{_stamp()} input loaded: {describe(img)}")
    return img


def predict_gb(layer_key: str, image: np.ndarray, cfg: dict) -> float:
    n = int(image.shape[0]) * int(image.shape[1])
    if layer_key == "palette" and cfg.get("source", "generate") == "generate":
        k = int(cfg.get("colours", 24))
        return n * k * BYTES_PER_PX_PER_COLOUR / (1 << 30)
    if layer_key == "scale":
        z = max(1, int(cfg.get("upscale", 1)))
        return n * z * z * image.shape[2] * 3 / (1 << 30)
    return n * image.shape[2] * 8 / (1 << 30)


def settle(log: Log, seconds: float, why: str) -> None:
    """Stand still long enough for the sampler to see a resting level.

    Layers run in milliseconds, so back to back their costs land inside one
    sampling interval and read as a single figure. A pause between them puts
    a flat stretch on either side of each step, which is what makes a rise
    attributable to the layer that caused it.
    """
    if seconds <= 0:
        return
    time.sleep(seconds)
    log.event(f"settled {seconds:.2f}s ({why})")


def cache_state() -> str:
    from pipeline.definitive import cache

    return (f"prepare={cache.CACHE.stats()['entries']}"
            f"/{cache.CACHE.stats()['bytes'] / 1e6:.2f}MB "
            f"snapshots={cache.SNAPSHOTS.stats()['entries']}"
            f"/{cache.SNAPSHOTS.stats()['bytes'] / 1e6:.2f}MB")


def drop_caches(log: Log) -> None:
    """Empty both caches, so the next layer carries nothing from the last."""
    from pipeline.definitive import cache

    before = cache_state()
    cache.CACHE.clear()
    cache.SNAPSHOTS.clear()
    freed = gc.collect()
    log.line(f"{_stamp()}   caches popped: {before} -> {cache_state()} "
             f"(gc freed {freed})")


def run_layer(spec, image: np.ndarray, cfg: dict, log: Log,
              outdir: Path, args) -> np.ndarray:
    key = spec.key
    predicted = predict_gb(key, image, cfg)

    log.line("")
    log.line(f"{_stamp()} --- layer '{key}' ---")
    log.line(f"{_stamp()}   input:     {describe(image)}")
    log.line(f"{_stamp()}   config:    {json.dumps(cfg, default=str)}")
    log.line(f"{_stamp()}   predicted: {predicted:.4f} GB peak intermediate")

    if predicted >= MAX_PREDICTED_GB and not args.force:
        log.line(f"{_stamp()}   REFUSED: predicted {predicted:.2f} GB exceeds the "
                 f"{MAX_PREDICTED_GB:.2f} GB harness limit. Not executed.")
        return image

    log.event(f"layer '{key}' BEFORE")

    inputs = {"image": image, "palettes": None}
    prep = {}

    # --- prepare ---------------------------------------------------------
    if spec.prepare is not None:
        t = time.perf_counter()
        try:
            with Alarm(MAX_LAYER_SECONDS, f"{key}.prepare"):
                if args.use_cache:
                    from pipeline.definitive.run import prepare_for
                    prep = prepare_for(spec, inputs, cfg, use_cache=True)
                else:
                    prep = spec.prepare(inputs, cfg)
        except Exception as e:                                  # noqa: BLE001
            log.line(f"{_stamp()}   prepare RAISED {type(e).__name__}: {e}")
            return image
        log.line(f"{_stamp()}   prepare done in {time.perf_counter()-t:.3f}s "
                 f"-> {json.dumps({k: str(v)[:60] for k, v in prep.items()})}")
        log.event(f"layer '{key}' after prepare")

    # --- apply -----------------------------------------------------------
    t = time.perf_counter()
    try:
        with Alarm(MAX_LAYER_SECONDS, f"{key}.apply"):
            produced = spec.apply(inputs, cfg, prep)
    except Exception as e:                                      # noqa: BLE001
        log.line(f"{_stamp()}   apply RAISED {type(e).__name__}: {e}")
        log.line(traceback.format_exc())
        return image
    elapsed = time.perf_counter() - t

    out = produced.pop("image")
    log.line(f"{_stamp()}   apply done in {elapsed:.3f}s")
    log.line(f"{_stamp()}   output:    {describe(out)}")
    if produced:
        log.line(f"{_stamp()}   reported:  "
                 f"{json.dumps({k: str(v)[:60] for k, v in produced.items()})}")

    growth = out.nbytes / max(1, image.nbytes)
    log.line(f"{_stamp()}   size change: {image.nbytes/1e6:.2f}MB -> "
             f"{out.nbytes/1e6:.2f}MB ({growth:.2f}x)")

    log.event(f"layer '{key}' AFTER")
    log.line(f"{_stamp()}   caches: {cache_state()}")

    # --- intermediate to disk, not held in RAM ---------------------------
    if args.save_intermediates:
        dest = outdir / f"{key}.npy"
        np.save(dest, out)
        log.line(f"{_stamp()}   wrote intermediate -> {dest} "
                 f"({dest.stat().st_size/1e6:.2f}MB on disk)")

    # --- release, then measure what survived -----------------------------
    del inputs, prep, produced
    freed = gc.collect()
    log.event(f"layer '{key}' after gc (freed {freed})")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--layer", help="run exactly ONE layer by key")
    p.add_argument("--all", action="store_true",
                   help="walk the default stack, one layer at a time")
    p.add_argument("--list", action="store_true", help="list layers and exit")
    p.add_argument("--input", type=Path,
                   help="a REAL image to centre-crop, instead of synthetic content")
    p.add_argument("--size", type=int, default=64,
                   help=f"input edge in px (default 64, max {MAX_EDGE})")
    p.add_argument("--colours", type=int, default=8,
                   help="palette size when the palette layer runs (default 8)")
    p.add_argument("--upscale", type=int, default=1,
                   help="zoom when the scale layer runs (default 1 = off)")
    p.add_argument("--save-intermediates", action="store_true",
                   help="write each layer's output to disk")
    p.add_argument("--outdir", type=Path,
                   default=Path("/tmp/definitive_trace"))
    p.add_argument("--log", type=Path, help="append the timeline here, fsynced")
    p.add_argument("--json", type=Path, help="write samples as JSON")
    p.add_argument("--force", action="store_true",
                   help="DANGEROUS: run past the predicted-cost refusal")
    p.add_argument("--use-cache", action="store_true",
                   help="prepare through the same cache apply_stack uses, so "
                        "the cache columns and --drop-caches mean something. "
                        "Off by default: a direct call is what isolates a "
                        "layer's own cost")
    p.add_argument("--pace", type=float, default=0.0, metavar="SECONDS",
                   help="stand still this long after each layer, and again "
                        "after popping caches (try 0.5)")
    p.add_argument("--drop-caches", action="store_true",
                   help="empty the prepare and snapshot caches between layers, "
                        "so nothing a layer measured is reused by the next")
    p.add_argument("--set", action="append", default=[], metavar="LAYER.KEY=VAL",
                   help="override one layer setting, e.g. --set palette.fit=true "
                        "or --set grid.enabled=false. Repeatable.")
    args = p.parse_args()

    def _coerce(text: str):
        low = text.lower()
        if low in ("true", "false"):
            return low == "true"
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                pass
        return text

    overrides: dict[str, dict] = {}
    for item in args.set:
        if "=" not in item or "." not in item.split("=")[0]:
            p.error(f"--set wants LAYER.KEY=VALUE, got {item!r}")
        path, _, value = item.partition("=")
        layer_key, _, field = path.partition(".")
        overrides.setdefault(layer_key, {})[field] = _coerce(value)

    args.outdir.mkdir(parents=True, exist_ok=True)
    log = Log(args.log)

    from pipeline.definitive.layers import REGISTRY

    if args.list:
        print(f"{'key':<14}{'order':>6}  label")
        for spec in sorted(REGISTRY.values(), key=lambda s: s.order):
            print(f"{spec.key:<14}{spec.order:>6}  {spec.label}")
        return 0

    if not args.layer and not args.all:
        p.error("give --layer KEY, or --all, or --list")

    log.line("=" * 78)
    log.line(f"Definitive Layer trace  pid={os.getpid()}  "
             f"{platform.platform()}")
    log.line(f"python {sys.version.split()[0]}  numpy {np.__version__}  "
             f"PIL {Image.__version__}")
    log.line(f"threads capped to 1 via {', '.join(_THREAD_VARS)}")
    log.line(f"limits: edge<={MAX_EDGE} px<={MAX_PIXELS:,} "
             f"predicted<={MAX_PREDICTED_GB}GB "
             f"layer<={MAX_LAYER_SECONDS}s total<={MAX_TOTAL_SECONDS}s")
    log.line("")
    log.line("IF THE MACHINE DIES: do NOT rerun. The last line of this log is")
    log.line("the operation that was in flight. Then collect, in this order:")
    log.line("  ls -la /Library/Logs/DiagnosticReports/*.panic")
    log.line("  pmset -g log | grep -i 'shutdown cause'")
    log.line("  last reboot | head -5        # did uptime actually reset?")
    log.line("  ls /Library/Logs/DiagnosticReports/ | grep -i jetsam")
    log.line("=" * 78)

    tracemalloc.start()
    log.event("harness start")

    try:
        with Alarm(MAX_TOTAL_SECONDS, "whole run"):
            image = (load_input(args.input, args.size, log)
                     if args.input else make_input(args.size, log))
            log.event("input ready")

            if args.layer:
                spec = REGISTRY.get(args.layer)
                if spec is None:
                    log.line(f"no layer '{args.layer}'. "
                             f"Known: {sorted(REGISTRY)}")
                    return 2
                order = [spec]
            else:
                order = sorted(REGISTRY.values(), key=lambda s: s.order)

            for spec in order:
                cfg = spec.settings(None)
                if spec.key == "palette":
                    cfg["colours"] = args.colours
                if spec.key == "scale":
                    cfg["upscale"] = args.upscale
                if spec.key in overrides:
                    cfg.update(overrides[spec.key])
                    if cfg.get("enabled") is False:
                        log.line(f"{_stamp()} --- layer '{spec.key}' DISABLED "
                                 f"by --set, skipped ---")
                        continue
                nxt = run_layer(spec, image, cfg, log, args.outdir, args)
                if nxt is not image:
                    del image
                    gc.collect()
                image = nxt

                # one layer, settle, pop, settle - so each step's cost is read
                # against a flat line rather than against the next step
                settle(log, args.pace, f"after '{spec.key}'")
                if args.drop_caches:
                    drop_caches(log)
                    settle(log, args.pace, "after popping caches")

            log.line("")
            log.event("all layers complete")
            log.line(f"{_stamp()} final: {describe(image)}")

    except TimeoutError as e:
        log.line(f"{_stamp()} TIMEOUT: {e}")
        log.line(f"{_stamp()} last completed operation is the line above this one.")
        return 3
    except KeyboardInterrupt:
        log.line(f"{_stamp()} interrupted by user")
        return 130
    finally:
        if args.json and log.rows:
            args.json.write_text(json.dumps(log.rows, indent=2))
            log.line(f"{_stamp()} samples -> {args.json}")
        if tracemalloc.is_tracing():
            _cur, _tpeak = tracemalloc.get_traced_memory()
            log.line(f"{_stamp()} peak traced (python+numpy allocations): "
                     f"{_tpeak/1e6:.2f} MB")
            tracemalloc.stop()
        peak = max((r["rss_mb"] for r in log.rows), default=0)
        log.line(f"{_stamp()} peak RSS this process: {peak} MB")
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
