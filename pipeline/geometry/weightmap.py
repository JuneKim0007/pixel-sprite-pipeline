"""A painted emphasis map for one reference image, stored beside it."""

from __future__ import annotations

from pathlib import Path

import numpy as np

SUFFIX = ".weight.png"

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


SOURCES = ("auto", "none", "subject", "painted")

FLOOR = 0.0
CEILING = 1.0


def from_subject(image: Path, floor: float = FLOOR, ceiling: float = CEILING,
                 edge: int = EDGE) -> np.ndarray:
    """A map that says the figure and not the ground it was cut from.

    The identity references are cut from character sheets and carry their
    sheet's own backdrop, which IPAdapter pulls in with the character.
    """
    from PIL import Image

    from .framing import backdrop_of

    from scipy import ndimage

    with Image.open(image) as handle:
        pixels = np.asarray(handle.convert("RGB")).astype(int)
    subject = np.abs(pixels - backdrop_of(pixels)).sum(axis=2) > 60
    # A navy dress on a purple sheet is within the threshold, so the figure
    # came back hollow. What the border cannot reach is the figure.
    subject = ndimage.binary_fill_holes(
        ndimage.binary_closing(subject, np.ones((5, 5))))
    small = np.asarray(
        Image.fromarray((subject * 255).astype(np.uint8)).resize(
            (edge, edge), Image.BILINEAR)).astype(np.float32) / 255.0
    return (floor + (ceiling - floor) * small).astype(np.float32)


def resolve(image: Path, source: str = "auto", floor: float = FLOOR,
            ceiling: float = CEILING) -> np.ndarray | None:
    """The map a run should use for one reference, or None for no masking."""
    if source == "none":
        return None
    if source == "subject":
        return from_subject(image, floor, ceiling)
    saved = load(image)
    if source == "painted":
        return saved
    return saved


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
