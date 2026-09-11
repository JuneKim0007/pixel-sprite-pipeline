#!/usr/bin/env python3
"""Key the backdrop out of a training image and drop what it leaves behind.

`pixelize.background_to_alpha` floods from the frame's edge and compares every
pixel to its SEED's colour. On a JPEG the seeds are the most compressed pixels
in the image, so the flood clears a two-pixel border and stops; on a
checkerboard it cannot cross between the two tones at all. This keys the
colours the border ring actually contains instead, then hands the remainder to
that flood.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.definitive import pixelize as px  # noqa: E402

RING = 0.03
QUANTISE = 4
MIN_SHARE = 0.04
MAX_COLOURS = 6
TOLERANCE = 30
TOLERANCES = (30, 45, 60)
SURVIVED = 0.85
FLOOR_KEEP = 0.05
MIN_PART = 0.04
ROUNDS = 3
MIN_PANEL = 0.20
KEEP_FLOOR = 0.07
MIN_TONE = 0.02


def ring_colours(rgb: np.ndarray, ring: float = RING, quant: int = QUANTISE,
                 min_share: float = MIN_SHARE, limit: int = MAX_COLOURS,
                 alpha: np.ndarray | None = None) -> list[tuple[int, int, int]]:
    """The colours the current boundary is made of, commonest first.

    Round one samples the frame's edge. Later rounds sample the edge of what
    is still opaque, so a blue panel inset behind a white border becomes the
    boundary once the border has gone.
    """
    h, w = rgb.shape[:2]
    k = max(2, int(round(min(h, w) * ring)))

    if alpha is None or not (alpha == 0).any():
        band = np.concatenate([rgb[:k].reshape(-1, 3), rgb[-k:].reshape(-1, 3),
                               rgb[:, :k].reshape(-1, 3), rgb[:, -k:].reshape(-1, 3)])
    else:
        from scipy import ndimage

        near = ndimage.binary_dilation(alpha == 0, np.ones((3, 3)), iterations=k)
        band = rgb[near & (alpha > 0)].reshape(-1, 3)
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
        # The cluster's true mean, not the quantised bin's corner.
        out.append(tuple(int(v) for v in band[flat == key].mean(axis=0).round()))
    return out


def backdrop_mask(rgb: np.ndarray, colours, tol: int) -> np.ndarray:
    """Every pixel matching any backdrop colour, as one mask."""
    hit = np.zeros(rgb.shape[:2], bool)
    a = rgb.astype(np.int16)
    for colour in colours:
        hit |= np.abs(a - np.asarray(colour, np.int16)).max(axis=2) <= tol
    return hit


def _touching_border(mask: np.ndarray) -> np.ndarray:
    """The part of `mask` reachable from the frame's edge, so interiors survive.

    Keying a colour globally removes white hair along with a white border.
    Flooding the UNION of the backdrop's colours keeps both facts: a
    checkerboard is one connected region and goes entirely, while a white
    shirt enclosed by a jacket never connects to the edge and stays.
    """
    from scipy import ndimage

    labels, n = ndimage.label(mask)
    if n == 0:
        return mask
    edge = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    return np.isin(labels, [i for i in np.unique(edge) if i])


def key_backdrop(rgb: np.ndarray, tol: int = TOLERANCE, rounds: int = ROUNDS,
                 min_panel: float = MIN_PANEL) -> np.ndarray:
    """Clear the frame's backdrop, then any large flat panel it was hiding.

    Round one takes every colour the frame's edge contains at once. Later
    rounds cannot: once the backdrop is gone the boundary IS the character,
    and keying what it samples there strips the figure. So a later round
    accepts a colour only when that one colour clears a panel worth of area -
    which a blue card behind a white border does, and an outline never does.
    """
    alpha = np.full(rgb.shape[:2], 255, np.uint8)
    area = alpha.size

    colours = ring_colours(rgb)
    if colours:
        alpha[_touching_border(backdrop_mask(rgb, colours, tol))] = 0

    for _ in range(max(0, rounds - 1)):
        # A decorated panel is several tones - six blues on _ (8) - and no one
        # of them is a panel by itself. Judge the round's total, not each.
        trial = alpha.copy()
        for colour in ring_colours(rgb, alpha=alpha):
            gone = _touching_border(
                backdrop_mask(rgb, [colour], tol) | (trial == 0))
            if (gone.sum() - (trial == 0).sum()) / area < MIN_TONE:
                continue
            if 1.0 - gone.mean() < KEEP_FLOOR:
                continue
            trial[gone] = 0
        added = ((trial == 0).sum() - (alpha == 0).sum()) / area
        if added < min_panel:
            break
        alpha = trial
    return alpha


def largest_parts(alpha: np.ndarray, min_part: float = MIN_PART) -> np.ndarray:
    """Keep the subject and anything of its size; drop text, marks and sparkles."""
    from scipy import ndimage

    labels, n = ndimage.label(alpha > 0)
    if n <= 1:
        return alpha
    sizes = ndimage.sum(alpha > 0, labels, range(1, n + 1))
    keep = {i + 1 for i, s in enumerate(sizes) if s >= sizes.max() * min_part}
    return np.where(np.isin(labels, list(keep)), alpha, 0).astype(np.uint8)


def clean(path: Path, tol: int = TOLERANCE, parts: bool = True) -> np.ndarray:
    """Raise the tolerance until the backdrop actually goes, never past sense.

    A one-pixel anti-aliased seam between a white border and the panel behind
    it matches neither colour and blocks the flood. Bridging it with a
    dilation also bridges a character's outline and eats the figure, so the
    tolerance is raised instead and the outcome is checked.
    """
    with Image.open(path) as im:
        rgb = np.asarray(im.convert("RGB"))

    # Judge against the figure's own area, not a flat number: a witch who
    # fills her frame keeps 63% legitimately, and pushing her further ate her.
    from pipeline.geometry import framing

    box = framing.measure(rgb)
    room = ((box.box_width * box.box_height) / (rgb.shape[0] * rgb.shape[1])
            if box else 1.0)

    alpha = None
    for step in TOLERANCES:
        trial = key_backdrop(rgb, step)
        kept = float((trial > 0).mean())
        if kept < FLOOR_KEEP:
            break                      # this step ate the subject; keep the last
        alpha = trial
        if kept / max(room, 1e-6) <= SURVIVED:
            break                      # backdrop is gone; do not push further
    if alpha is None:
        alpha = key_backdrop(rgb, tol)
    # The flood reaches a backdrop shade the ring never sampled.
    flooded = px.background_to_alpha(np.dstack([rgb, alpha])[..., :3], 14)
    alpha = np.minimum(alpha, flooded[..., 3])
    if parts:
        alpha = largest_parts(alpha)
    return np.dstack([rgb, alpha])


def main() -> int:
    import argparse
    import csv

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=ROOT / "training_set")
    ap.add_argument("--out", type=Path, default=ROOT / "training_set" / "temp")
    ap.add_argument("--tolerance", type=int, default=TOLERANCE)
    ap.add_argument("--keep-parts", action="store_true",
                    help="leave detached elements in place")
    a = ap.parse_args()

    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for bucket in ("128x128", "256x256"):
        folder = a.src / bucket
        if not folder.is_dir():
            continue
        files = sorted(p for p in folder.iterdir()
                       if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
        for i, p in enumerate(files, 1):
            name = f"{bucket}_{i:02d}.png"
            rgba = clean(p, a.tolerance, parts=not a.keep_parts)
            Image.fromarray(rgba, "RGBA").save(a.out / name)
            kept = float((rgba[..., 3] > 0).mean())
            rows.append({"name": name, "bucket": bucket,
                         "source": p.name, "kept": f"{kept:.3f}"})
            print(f"  {name}  {kept:>5.0%} kept   <- {p.name}")

    with open(a.out / "index.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "bucket", "source", "kept"])
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} written to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
