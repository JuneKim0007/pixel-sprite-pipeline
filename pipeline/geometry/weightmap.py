"""A painted emphasis map for one reference image, stored beside it."""

from __future__ import annotations

from pathlib import Path

import numpy as np

SUFFIX = ".weight.png"

# What a conditioning mask becomes: samplers.py resizes it to the latent grid,
# which is an eighth of the image, then multiplies. Storing it at that size
# keeps the file small and loses nothing the sampler would have kept.
EDGE = 128

NEUTRAL = 0.8


def sidecar_for(image: Path) -> Path:
    return image.with_suffix(image.suffix + SUFFIX)


def load(image: Path) -> np.ndarray | None:
    path = sidecar_for(image)
    if not path.exists():
        return None
    from PIL import Image

    with Image.open(path) as handle:
        return np.asarray(handle.convert("L"), dtype=np.float32) / 255.0


def save(image: Path, weights: np.ndarray) -> Path:
    from PIL import Image

    clipped = np.clip(np.asarray(weights, dtype=np.float32), 0.0, 1.0)
    path = sidecar_for(image)
    Image.fromarray((clipped * 255.0 + 0.5).astype(np.uint8), mode="L").save(path)
    return path


def clear(image: Path) -> bool:
    path = sidecar_for(image)
    if not path.exists():
        return False
    path.unlink()
    return True


def flat(value: float = NEUTRAL, edge: int = EDGE) -> np.ndarray:
    return np.full((edge, edge), float(value), dtype=np.float32)


def radial(centre: float = 0.9, edge_value: float = 0.8, falloff: float = 1.0,
           edge: int = EDGE) -> np.ndarray:
    """Strongest at the middle, easing to `edge_value` at the corners."""
    axis = np.linspace(-1.0, 1.0, edge, dtype=np.float32)
    grid = np.hypot(*np.meshgrid(axis, axis))
    reach = np.clip(grid / max(falloff, 1e-6), 0.0, 1.0)
    return (centre + (edge_value - centre) * reach).astype(np.float32)


def describe(weights: np.ndarray | None) -> dict:
    if weights is None:
        return {"painted": False}
    return {
        "painted": True,
        "edge": int(weights.shape[0]),
        "min": round(float(weights.min()), 3),
        "max": round(float(weights.max()), 3),
        "mean": round(float(weights.mean()), 3),
    }
