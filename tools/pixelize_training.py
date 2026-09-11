#!/usr/bin/env python3
"""Reduce a cleaned training image to a sprite canvas on at most N colours.

Why the two orders differ: docs/downloaded-art-to-sprites.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.definitive import pixelize as px  # noqa: E402
from pipeline.geometry import framing  # noqa: E402

COLOURS = 12
FILL = 0.92


def canvas_for(bucket: str, aspect: float) -> tuple[int, int]:
    """256 is square; 128 gains height rather than squashing a standing figure."""
    if bucket.startswith("256"):
        return 256, 256
    if aspect <= 1.15:
        return 128, 128
    return (128, 160) if aspect <= 1.45 else (128, 192)


def _crop_to_subject(im: Image.Image) -> Image.Image:
    box = im.getbbox()
    return im.crop(box) if box else im


def _quantise(rgba: np.ndarray, colours: int) -> np.ndarray:
    rgb, alpha = rgba[..., :3], rgba[..., 3]
    if not (alpha > 0).any():
        return rgba
    pal = px.generate_palette(rgb, colours, alpha=alpha)
    fitted = px.apply_fixed_palette(rgb, pal)
    return np.dstack([fitted, alpha])


def reduce_then_quantise(im: Image.Image, canvas, colours=COLOURS) -> np.ndarray:
    seated = framing.seat(_crop_to_subject(im), canvas, FILL)
    return _quantise(np.asarray(seated), colours)


def quantise_then_reduce(im: Image.Image, canvas, colours=COLOURS) -> np.ndarray:
    big = _quantise(np.asarray(_crop_to_subject(im)), colours)
    seated = framing.seat(Image.fromarray(big, "RGBA"), canvas, FILL)
    # Averaging palette entries invents colours between them; fit again.
    return _quantise(np.asarray(seated), colours)


ORDERS = {"reduce_first": reduce_then_quantise,
          "quantise_first": quantise_then_reduce}


def distinct(rgba: np.ndarray) -> int:
    rgb, a = rgba[..., :3], rgba[..., 3]
    if not (a > 0).any():
        return 0
    return len(np.unique(rgb[a > 0].reshape(-1, 3), axis=0))


def main() -> int:
    import argparse
    import csv

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=ROOT / "training_set")
    ap.add_argument("--out", type=Path, default=ROOT / "training_set" / "sprites")
    ap.add_argument("--colours", type=int, default=COLOURS)
    ap.add_argument("--order", choices=sorted(ORDERS), default="reduce_first",
                    help="reduce_first won on 32 of 35; see the doc")
    a = ap.parse_args()

    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for bucket in ("128x128", "256x256"):
        folder = a.src / bucket
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.png")):
            im = Image.open(p).convert("RGBA")
            box = im.getbbox()
            aspect = (box[3] - box[1]) / (box[2] - box[0]) if box else 1.0
            canvas = canvas_for(bucket, aspect)
            out = ORDERS[a.order](im, canvas, a.colours)
            Image.fromarray(out, "RGBA").save(a.out / p.name)
            rows.append({"name": p.name, "canvas": f"{canvas[0]}x{canvas[1]}",
                         "colours": distinct(out),
                         "aspect": f"{aspect:.2f}"})
            print(f"  {p.name}  {canvas[0]}x{canvas[1]}  {distinct(out)} colours")

    with open(a.out / "sprites.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "canvas", "colours", "aspect"])
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} sprites written to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
