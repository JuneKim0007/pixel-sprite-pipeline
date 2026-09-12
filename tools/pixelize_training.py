#!/usr/bin/env python3

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import definitive  # noqa: E402
from pipeline.definitive import pixelize as px  # noqa: E402
from pipeline.shared import canvas as canvas_mod  # noqa: E402

COLOURS = 10
FILL = 0.92
REDUCE = "median"
CONTRAST = 1.12
TARGET_LUMA = 132
MAX_LIFT = 0.08
LUMA = np.array([0.299, 0.587, 0.114], np.float32)


CANVASES = canvas_mod.LADDER


def native_factor(subject: np.ndarray) -> int:
    return max(1, px.detect_block(subject[..., :3]))


def plan_for(subject: np.ndarray) -> tuple[int, tuple[int, int]]:
    """Reduce by the art's OWN block size, then take a canvas that fits it.

    Forcing a factor chosen to hit a preset canvas samples across the source's
    block boundaries whenever the two disagree - measured, 13 of 35 - and no
    phase is correct for a factor that is not the art's own.
    """
    k = native_factor(subject)
    while True:
        fit = canvas_mod.fitting(subject.shape[1] // k, subject.shape[0] // k)
        if fit is not None:
            return k, fit
        k += 1


def auto_brightness(rgba: np.ndarray, target: float = TARGET_LUMA,
                    cap: float = MAX_LIFT) -> float:
    opaque = rgba[..., 3] > 0
    if not opaque.any():
        return 0.0
    luma = float((rgba[..., :3][opaque].astype(np.float32) @ LUMA).mean())
    return float(np.clip((target - luma) / 255.0, 0.0, cap))


def block_factor(shape: tuple[int, ...], canvas: tuple[int, int],
                 fill: float = FILL) -> int:
    return max(1, math.ceil(max(shape[0] / canvas[1], shape[1] / canvas[0])))


def stack_for(subject: np.ndarray, canvas: tuple[int, int], *,
              factor: int = 0,
              reduce: str = REDUCE, contrast: float = CONTRAST,
              brightness: float = 0.0, colours: int = COLOURS,
              fill: float = FILL, keep_black: bool = False,
              keep_white: bool = False) -> list[dict]:
    return [
        {"layer": "curves",
         "config": {"contrast": contrast, "brightness": brightness}},
        {"layer": "grid",
         "config": {"factor": factor or block_factor(subject.shape, canvas, fill),
                    "phase": "auto", "reduce": reduce}},
        {"layer": "palette",
         "config": {"source": "generate", "colours": colours,
                    "preserve_black": keep_black, "preserve_white": keep_white}},
        {"layer": "canvas",
         "config": {"width": canvas[0], "height": canvas[1], "fill": 1.0,
                    "binary_alpha": True}},
    ]


def sprite(im: Image.Image, canvas: tuple[int, int] | None = None,
           **kw) -> np.ndarray:
    subject = np.asarray(im.crop(im.getbbox()))
    if canvas is None:
        kw["factor"], canvas = plan_for(subject)
    out, _ = definitive.apply_stack(
        subject, stack_for(subject, canvas, **kw), use_cache=False)
    if out.shape[:2] == (canvas[1], canvas[0]):
        return out
    seated = Image.new("RGBA", canvas, (0, 0, 0, 0))
    seated.paste(Image.fromarray(np.ascontiguousarray(out), "RGBA"),
                 ((canvas[0] - out.shape[1]) // 2,
                  (canvas[1] - out.shape[0]) // 2))
    return np.asarray(seated)


def distinct(rgba: np.ndarray) -> int:
    rgb, alpha = rgba[..., :3], rgba[..., 3]
    if not (alpha > 0).any():
        return 0
    return len(np.unique(rgb[alpha > 0].reshape(-1, 3), axis=0))


def main() -> int:
    import argparse
    import csv

    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=ROOT / "training_set")
    ap.add_argument("--out", type=Path, default=ROOT / "training_set" / "sprite")
    ap.add_argument("--colours", type=int, default=COLOURS)
    ap.add_argument("--reduce", default=REDUCE, choices=px.REDUCE_MODES)
    ap.add_argument("--contrast", type=float, default=CONTRAST)
    ap.add_argument("--keep-black", action="store_true")
    ap.add_argument("--keep-white", action="store_true")
    a = ap.parse_args()

    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for bucket in ("128x128", "256x256"):
        folder = a.src / "keyed" / bucket
        if not folder.is_dir():
            continue
        for p in sorted(folder.glob("*.png")):
            im = Image.open(p).convert("RGBA")
            subject = np.asarray(im.crop(im.getbbox()))
            factor, canvas = plan_for(subject)
            lift = auto_brightness(np.asarray(im))
            out = sprite(im, canvas, factor=factor, reduce=a.reduce,
                         contrast=a.contrast, brightness=lift,
                         colours=a.colours, keep_black=a.keep_black,
                         keep_white=a.keep_white)
            Image.fromarray(out, "RGBA").save(a.out / p.name)
            rows.append({"name": p.name, "canvas": f"{canvas[0]}x{canvas[1]}",
                         "colours": distinct(out), "factor": factor,
                         "brightness": f"{lift * 255:+.0f}"})
            print(f"  {p.name}  {canvas[0]}x{canvas[1]}  {distinct(out)} colours"
                  f"  brightness {lift * 255:+.0f}")

    with open(a.out / "sprites.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "canvas", "colours",
                                           "factor", "brightness"])
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} sprites written to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
