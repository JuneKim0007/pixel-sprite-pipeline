"""Where the subject sits in its frame, measured rather than asked for."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

CORNER = 20
TOLERANCE = 60
RING = 0.03
QUANTISE = 4
MIN_SHARE = 0.04
MAX_COLOURS = 6
MIN_PART = 0.04
KEY_TOLERANCE = 30
ROUNDS = 3
MIN_PANEL = 0.20
KEEP_FLOOR = 0.07
MIN_TONE = 0.02


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


def backdrop_colours(pixels: np.ndarray, ring: float = RING,
                     quant: int = QUANTISE, min_share: float = MIN_SHARE,
                     limit: int = MAX_COLOURS,
                     alpha: np.ndarray | None = None) -> list[tuple[int, int, int]]:
    """Every colour the border is made of, commonest first.

    backdrop_of takes one median and cannot describe a checkerboard or a JPEG.
    """
    height, width = pixels.shape[:2]
    k = max(2, int(round(min(height, width) * ring)))

    if alpha is None or not (alpha == 0).any():
        band = np.concatenate([pixels[:k].reshape(-1, 3), pixels[-k:].reshape(-1, 3),
                               pixels[:, :k].reshape(-1, 3), pixels[:, -k:].reshape(-1, 3)])
    else:
        from scipy import ndimage

        near = ndimage.binary_dilation(alpha == 0, np.ones((3, 3)), iterations=k)
        band = pixels[near & (alpha > 0)].reshape(-1, 3)
    if len(band) < 50:
        return []

    shift = 8 - quant
    keys = (band >> shift).astype(np.uint32)
    flat = (keys[:, 0] << (2 * quant)) | (keys[:, 1] << quant) | keys[:, 2]
    found, counts = np.unique(flat, return_counts=True)
    out = []
    for key, n in sorted(zip(found, counts), key=lambda t: -t[1])[:limit]:
        if n / len(flat) < min_share:
            break
        out.append(tuple(int(v) for v in band[flat == key].mean(axis=0).round()))
    return out


def matching(pixels: np.ndarray, colours, tolerance: int) -> np.ndarray:
    hit = np.zeros(pixels.shape[:2], bool)
    a = pixels.astype(np.int16)
    for colour in colours:
        hit |= np.abs(a - np.asarray(colour, np.int16)).max(axis=2) <= tolerance
    return hit


def touching_border(mask: np.ndarray) -> np.ndarray:
    """The part of `mask` reachable from the frame's edge, so interiors survive."""
    from scipy import ndimage

    labels, n = ndimage.label(mask)
    if n == 0:
        return mask
    edge = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    return np.isin(labels, [i for i in np.unique(edge) if i])


def largest_parts(mask: np.ndarray, min_part: float = MIN_PART) -> np.ndarray:
    """Keep the subject and anything of its size; drop text, marks and sparkles."""
    from scipy import ndimage

    labels, n = ndimage.label(mask > 0)
    if n <= 1:
        return mask
    sizes = ndimage.sum(mask > 0, labels, range(1, n + 1))
    keep = [i + 1 for i, size in enumerate(sizes) if size >= sizes.max() * min_part]
    return np.where(np.isin(labels, keep), mask, 0).astype(mask.dtype)


def _rescaled(art, factor: float):
    """Premultiplied: resizing RGBA straight leaves a dark fringe at every edge."""
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


def square_for(art, fill: float) -> tuple[int, int]:
    """The square on which `art` would span `fill`, without resampling it."""
    edge = round(max(art.width, art.height) / fill)
    return edge, edge


def seat(art, canvas: tuple[int, int], fill: float, background=None):
    """Centre `art` on `canvas`, its longest side scaled to span `fill` of it."""
    from PIL import Image

    factor = min(canvas[0] * fill / art.width, canvas[1] * fill / art.height)
    if abs(factor - 1.0) > 1e-3:
        art = _rescaled(art, factor)
    out = Image.new(art.mode, canvas, background if background else (0, 0, 0, 0))
    out.paste(art, ((canvas[0] - art.width) // 2, (canvas[1] - art.height) // 2))
    return out


def key_backdrop(pixels: np.ndarray, tolerance: int = KEY_TOLERANCE,
                 rounds: int = ROUNDS, min_panel: float = MIN_PANEL) -> np.ndarray:
    """Clear the frame's backdrop, then any large flat panel it was hiding.

    See docs/downloaded-art-to-sprites.md for why a later round judges by area.
    """
    alpha = np.full(pixels.shape[:2], 255, np.uint8)
    area = alpha.size

    colours = backdrop_colours(pixels)
    if colours:
        alpha[touching_border(matching(pixels, colours, tolerance))] = 0

    for _ in range(max(0, rounds - 1)):
        trial = alpha.copy()
        for colour in backdrop_colours(pixels, alpha=alpha):
            gone = touching_border(
                matching(pixels, [colour], tolerance) | (trial == 0))
            if (gone.sum() - (trial == 0).sum()) / area < MIN_TONE:
                continue
            if 1.0 - gone.mean() < KEEP_FLOOR:
                continue
            trial[gone] = 0
        if ((trial == 0).sum() - (alpha == 0).sum()) / area < min_panel:
            break
        alpha = trial
    return alpha
