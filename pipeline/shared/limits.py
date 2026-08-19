"""How much of the machine the EDITOR (not the pipeline subprocess) may take, as shares of what it has. memory_share sizes one array, not the process - 14.9 GB python3.12 on 16 GB still obeyed it."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

SHARES: dict[str, float] = {
    "cpu_share": 0.4,
    "memory_share": 0.05,
    "preview_ms": 150.0,
    "concurrent": 1.0,
    "output_share": 0.05,
    "rss_share": 0.35,
}

# Six live copies at 4 bytes, so a result is budgeted at 24 bytes per pixel and not at 4.
BYTES_PER_OUTPUT_PIXEL = 24

_STATE = dict(SHARES)
_APPLIED: bool | None = None
_BENCH: dict | None = None

_THREAD_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def cores() -> int:
    try:
        return len(os.sched_getaffinity(0))
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
    budget = memory_bytes() * float(_STATE["memory_share"])
    # 256 palette entries is the maximum the editor allows; size for the worst case rather than the current palette, so the bound holds when it changes.
    per_colour = 256 * 3 * 4
    return int(max(1024, min(65536, budget / per_colour / 8)))


def output_pixels() -> int:
    budget = memory_bytes() * float(_STATE["output_share"])
    return int(max(1 << 20, budget / BYTES_PER_OUTPUT_PIXEL))


def rss_bytes() -> int:
    return int(memory_bytes() * float(_STATE["rss_share"]))


def preview_edge() -> int:
    """Below 256 a preview stops showing what it is for; above 1024 there is nothing to gain because the source is rarely larger."""
    bench = benchmark()
    budget_s = float(_STATE["preview_ms"]) / 1000.0
    megapixels = budget_s / max(bench["seconds_per_megapixel"], 1e-6)
    edge = int((megapixels * 1e6) ** 0.5)
    return max(256, min(1024, edge - edge % 32))


def _cache_file() -> Path:
    return Path(__file__).resolve().parent.parent.parent / ".run" / "benchmark.json"


def benchmark(force: bool = False) -> dict:
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

    # Under noise every pixel is its own colour - a worst case that never occurs, and benchmarking it under-reported this machine badly enough to produce a 320 px preview.
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
        pass
    return _BENCH


def apply(**overrides) -> None:
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
        "output_pixels": output_pixels,
        "rss_bytes": rss_bytes,
        "concurrent": lambda: max(1, int(_STATE["concurrent"])),
    }[name]()


def describe() -> dict:
    return {
        "shares": dict(_STATE),
        "machine": {"cores": cores(), "memory_gb": round(memory_bytes() / (1 << 30), 1)},
        "derived": {"threads": threads(), "colour_chunk": colour_chunk(),
                    "preview_edge": preview_edge(),
                    "output_pixels": output_pixels(),
                    "rss_bytes": rss_bytes(),
                    "concurrent": get("concurrent")},
        "seconds_per_megapixel": round(benchmark()["seconds_per_megapixel"], 4),
        "in_time": _APPLIED,
        "gpu": "not capped; bounded only by how much work is submitted",
    }
