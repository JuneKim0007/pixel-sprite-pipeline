"""Where the subject sits in its frame, measured rather than asked for."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..shared.canvas import seat, square_for  # noqa: F401
from ..shared.keying import KEY_TOLERANCE, key_backdrop, largest_parts  # noqa: F401

CORNER = 20
TOLERANCE = 60


@dataclass
class Framing:
    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int
    backdrop: tuple[int, int, int]

    @property
    def box_height(self) -> int:
        return self.bottom - self.top

    @property
    def box_width(self) -> int:
        return self.right - self.left

    @property
    def fill(self) -> float:
        """The fraction of the frame's tightest axis the subject spans."""
        return max(self.box_height / self.height, self.box_width / self.width)

    @property
    def clipped(self) -> list[str]:
        """Edges the subject touches, where detail is already lost."""
        touching = []
        if self.top <= 0:
            touching.append("top")
        if self.left <= 0:
            touching.append("left")
        if self.bottom >= self.height - 1:
            touching.append("bottom")
        if self.right >= self.width - 1:
            touching.append("right")
        return touching

    def describe(self) -> str:
        where = ", ".join(self.clipped) if self.clipped else "none"
        return (f"fills {self.fill * 100:.0f}% of frame, "
                f"touching {where}")


def backdrop_of(pixels: np.ndarray) -> np.ndarray:
    k = CORNER
    corners = np.concatenate([
        pixels[:k, :k].reshape(-1, 3), pixels[:k, -k:].reshape(-1, 3),
        pixels[-k:, :k].reshape(-1, 3), pixels[-k:, -k:].reshape(-1, 3)])
    return np.median(corners, axis=0)


def stands_out(pixels: np.ndarray, backdrop: np.ndarray,
               tolerance: int = TOLERANCE) -> np.ndarray:
    return np.abs(pixels - backdrop).sum(axis=2) > tolerance


def measure(image: Path | np.ndarray, tolerance: int = TOLERANCE) -> Framing | None:
    """None when nothing stands out from the corners, which is an empty frame."""
    if isinstance(image, Path):
        from PIL import Image

        with Image.open(image) as handle:
            pixels = np.asarray(handle.convert("RGB")).astype(int)
    else:
        pixels = np.asarray(image).astype(int)

    height, width, _ = pixels.shape
    bg = backdrop_of(pixels)
    subject = stands_out(pixels, bg, tolerance)
    if not subject.any():
        return None

    ys, xs = np.nonzero(subject)
    return Framing(left=int(xs.min()), top=int(ys.min()),
                   right=int(xs.max()), bottom=int(ys.max()),
                   width=width, height=height,
                   backdrop=tuple(int(v) for v in bg))


def recentre(image: Path, target: float = 0.0, tolerance: int = TOLERANCE) -> str | None:
    """Centre the subject in its frame, padding with the backdrop it already has."""
    from PIL import Image

    box = measure(image, tolerance)
    if box is None or box.clipped:
        return None

    dx = (box.width - box.box_width) // 2 - box.left
    dy = (box.height - box.box_height) // 2 - box.top
    if target > 0:
        scale = target / box.fill
    else:
        scale = 1.0
    if abs(dx) < 2 and abs(dy) < 2 and abs(scale - 1.0) < 0.02:
        return None

    with Image.open(image) as handle:
        source = handle.convert("RGB")
    canvas = Image.new("RGB", (box.width, box.height), box.backdrop)

    crop = source.crop((box.left, box.top, box.right + 1, box.bottom + 1))
    if scale != 1.0:
        crop = crop.resize((max(1, round(crop.width * scale)),
                            max(1, round(crop.height * scale))), Image.NEAREST)
    canvas.paste(crop, ((box.width - crop.width) // 2,
                        (box.height - crop.height) // 2))
    canvas.save(image)
    return f"recentred by ({dx:+d}, {dy:+d})" + (
        f" and scaled x{scale:.2f}" if scale != 1.0 else "")


