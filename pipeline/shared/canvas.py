from __future__ import annotations

import numpy as np

WIDTHS = (64, 96, 128, 160, 192, 224, 256)
HEIGHTS = (64, 96, 128, 160, 192, 224, 256, 320, 384)
LADDER = sorted(((w, h) for w in WIDTHS for h in HEIGHTS if h >= w),
                key=lambda c: (c[0] * c[1], c[0]))


def fitting(width: int, height: int):
    """The smallest sprite canvas that holds this art, or None if none does."""
    return next((c for c in LADDER if width <= c[0] and height <= c[1]), None)


def square_for(art, fill: float, lattice: int = 0) -> tuple[int, int]:
    edge = round(max(art.width, art.height) / fill)
    if lattice:
        edge = -(-edge // lattice) * lattice
    return edge, edge


def rescaled(art, factor: float):
    from PIL import Image

    size = (max(1, round(art.width * factor)), max(1, round(art.height * factor)))
    if art.mode != "RGBA":
        return art.resize(size, Image.LANCZOS if factor > 1 else Image.BOX)

    a = np.asarray(art).astype(np.float32)
    alpha = a[..., 3:4] / 255.0
    pre = Image.fromarray(
        np.dstack([a[..., :3] * alpha, a[..., 3:4]]).astype(np.uint8), "RGBA")
    small = np.asarray(pre.resize(size, Image.BOX)).astype(np.float32)
    back = np.clip(small[..., 3:4] / 255.0, 1e-6, None)
    return Image.fromarray(np.dstack([
        np.clip(small[..., :3] / back, 0, 255),
        np.where(small[..., 3] >= 128, 255, 0)]).astype(np.uint8), "RGBA")


def seat(art, canvas: tuple[int, int], fill: float, background=None,
         shrink_only: bool = False, lattice: int = 0):
    """`lattice` snaps the paste offset, so art drawn on a grid keeps its phase."""
    from PIL import Image

    factor = min(canvas[0] * fill / art.width, canvas[1] * fill / art.height)
    if shrink_only:
        factor = min(factor, 1.0)
    if abs(factor - 1.0) > 1e-3:
        art = rescaled(art, factor)
    out = Image.new(art.mode, canvas, background if background else (0, 0, 0, 0))
    left, top = (canvas[0] - art.width) // 2, (canvas[1] - art.height) // 2
    if lattice:
        left, top = (left // lattice) * lattice, (top // lattice) * lattice
    out.paste(art, (left, top))
    return out
