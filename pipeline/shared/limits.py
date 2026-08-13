"""How much of the machine the editor may take, as a share of what it has.

These bound the EDITOR, not the pipeline. A generation run is a separate
process (subprocess.Popen from the run and queue routes) and is deliberately
left alone. What shares a process with the editor is the web server, whose only
CPU-heavy work is the editor - so capping this process costs nothing else.

Importing this module does nothing; only server.py calls apply().

Configuration is ratios and the absolutes are derived, because "half the cores"
means something different on four cores and on sixty-four:

    cpu_share      0.4   of the cores this process can see
    memory_share   0.05  of physical RAM, for one preview's working set
    preview_ms     150   budget for one interactive preview

preview_ms is a budget rather than a size. A one-time benchmark measures cost
per megapixel and solves for the largest edge that fits, so a faster machine
gets a bigger preview from the same setting.

GPU share is NOT capped. There is no portable mechanism and none at all on
Metal - no equivalent of CUDA_MPS_ACTIVE_THREAD_PERCENTAGE exists. The only
lever is how much work gets submitted, which is what preview_edge controls.

CPU capping is by environment variable: every BLAS respects it and it works
everywhere, but it bounds thread count rather than scheduling priority. cgroups
and QoS classes would be finer and neither is portable.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

SHARES: dict[str, float] = {
    "cpu_share": 0.4,        # of visible cores
    "memory_share": 0.05,    # of physical RAM, per preview working set
    "preview_ms": 150.0,     # budget for one interactive preview
    "concurrent": 1.0,       # previews in flight; two is always waste
}

_STATE = dict(SHARES)
# Whether numpy was already loaded when apply() ran. Recorded then, not asked
# later: by the time anyone reads this, numpy is always loaded, and the first
# version of the check therefore always said "too late".
_APPLIED: bool | None = None
_BENCH: dict | None = None

_THREAD_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",     # Accelerate, which is what MPS builds use
)


# ------------------------------------------------------------------ the machine


def cores() -> int:
    """Cores this process may actually use.

    sched_getaffinity where it exists: a container can give a process fewer
    cores than the machine has, and cpu_count will not notice.
    """
    try:
        return len(os.sched_getaffinity(0))          # Linux, and containers
    except AttributeError:
        pass
    try:
        return int(os.sysconf("SC_NPROCESSORS_ONLN"))
    except (ValueError, OSError, AttributeError):
        return os.cpu_count() or 4


def memory_bytes() -> int:
    """Physical RAM, or a conservative guess if the platform will not say."""
    try:
        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (ValueError, OSError, AttributeError):
        return 8 << 30


def threads() -> int:
    """Cores to allow. At least one, never all of them."""
    n = cores()
    want = int(round(n * float(_STATE["cpu_share"])))
    return max(1, min(want, max(1, n - 1)))


def colour_chunk() -> int:
    """Colours matched against the palette at once.

    The distance matrix is chunk x entries x 3 float32. Floored so the loop
    does not become the cost, ceilinged because past this the allocation stops
    being the bottleneck.
    """
    budget = memory_bytes() * float(_STATE["memory_share"])
    # 256 palette entries is the maximum the editor allows; size for the worst
    # case rather than the current palette, so the bound holds when it changes.
    per_colour = 256 * 3 * 4
    return int(max(1024, min(65536, budget / per_colour / 8)))


def preview_edge() -> int:
    """Longest side an interactive preview is computed at.

    Below 256 a preview stops showing what it is for; above 1024 there is
    nothing to gain because the source is rarely larger.
    """
    bench = benchmark()
    budget_s = float(_STATE["preview_ms"]) / 1000.0
    megapixels = budget_s / max(bench["seconds_per_megapixel"], 1e-6)
    edge = int((megapixels * 1e6) ** 0.5)
    return max(256, min(1024, edge - edge % 32))


# -------------------------------------------------------------------- benchmark


def _cache_file() -> Path:
    return Path(__file__).resolve().parent.parent.parent / ".run" / "benchmark.json"


def benchmark(force: bool = False) -> dict:
    """Cost per megapixel on this machine, measured once and remembered.

    Cached on disk, keyed by the machine's shape so a different machine or a
    changed core count re-measures instead of inheriting a number that was
    never about it.
    """
    global _BENCH
    if _BENCH is not None and not force:
        return _BENCH

    signature = f"{cores()}x{memory_bytes() >> 30}g"
    path = _cache_file()
    if not force and path.exists():
        try:
            saved = json.loads(path.read_text())
            if saved.get("signature") == signature:
                _BENCH = saved
                return saved
        except (OSError, ValueError):
            pass

    import numpy as np

    # Render-shaped data, not uniform noise. Under noise every pixel is its
    # own colour - a worst case that never occurs, and benchmarking it
    # under-reported this machine badly enough to produce a 320 px preview.
    edge = 384
    rng = np.random.default_rng(0)
    ramp = np.linspace(0, 255, edge, dtype=np.float32)
    field = (ramp[:, None] * 0.5 + ramp[None, :] * 0.5)[..., None] * np.array([1.0, 0.7, 0.4])
    sample = np.clip(field + rng.normal(0, 6, (edge, edge, 3)), 0, 255).astype(np.uint8)

    start = time.perf_counter()
    flat = sample.reshape(-1, 3)
    uniq, inverse = np.unique(flat, axis=0, return_inverse=True)
    ref = uniq[:: max(1, len(uniq) // 64)][:64].astype(np.float32)
    a = uniq.astype(np.float32)
    idx = ((a[:, None, :] - ref[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    ref[idx][inverse].reshape(sample.shape)
    elapsed = time.perf_counter() - start

    _BENCH = {
        "signature": signature,
        "seconds_per_megapixel": elapsed / (edge * edge / 1e6),
        "measured_at": time.time(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_BENCH, indent=2))
    except OSError:
        pass                          # a read-only checkout is not a failure
    return _BENCH


# ---------------------------------------------------------------------- applying


def apply(**overrides) -> None:
    """Cap this process. Call before numpy is imported.

    Anything already exported wins: someone who set OMP_NUM_THREADS meant it.
    """
    global _APPLIED
    for key, value in overrides.items():
        if key in SHARES and value is not None:
            _STATE[key] = float(value)
    import sys

    _APPLIED = "numpy" not in sys.modules
    for var in _THREAD_VARS:
        os.environ.setdefault(var, str(threads()))


def get(name: str) -> int:
    """The derived absolute for one limit."""
    return {
        "threads": threads,
        "colour_chunk": colour_chunk,
        "preview_edge": preview_edge,
        "concurrent": lambda: max(1, int(_STATE["concurrent"])),
    }[name]()


def describe() -> dict:
    return {
        "shares": dict(_STATE),
        "machine": {"cores": cores(), "memory_gb": round(memory_bytes() / (1 << 30), 1)},
        "derived": {"threads": threads(), "colour_chunk": colour_chunk(),
                    "preview_edge": preview_edge(),
                    "concurrent": get("concurrent")},
        "seconds_per_megapixel": round(benchmark()["seconds_per_megapixel"], 4),
        # None means apply() was never called - which is the correct state for
        # a pipeline process. False means it ran after numpy had already loaded
        # and therefore had no effect.
        "in_time": _APPLIED,
        # There is no portable GPU equivalent, and none at all on Metal.
        "gpu": "not capped; bounded only by how much work is submitted",
    }
