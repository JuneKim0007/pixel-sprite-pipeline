#!/usr/bin/env python3
"""Put a known colour behind the sprites, instead of letting the trainer pick.
kohya composites the hard alpha onto something unspecified, and the LoRA learns it."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.looks.vocabulary import BACKDROP  # noqa: E402
from pipeline.shared.colour import parse_colour  # noqa: E402


def flatten(src: Path, dst: Path, colour: tuple[int, int, int]) -> float:
    """Returns the share of the frame the subject occupies."""
    with Image.open(src) as handle:
        rgba = np.asarray(handle.convert("RGBA")).astype(np.uint8)

    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    under = np.empty_like(rgba[..., :3], dtype=np.float32)
    under[:] = np.asarray(colour, dtype=np.float32)
    # Straight alpha: the sprites were thresholded, so no fringe to premultiply.
    flat = rgba[..., :3].astype(np.float32) * alpha + under * (1.0 - alpha)

    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(flat.round().astype(np.uint8), "RGB").save(dst)
    return float((rgba[..., 3] > 0).mean())


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=ROOT / "training_set/sprite")
    ap.add_argument("--out", type=Path, default=ROOT / "training_set/flat")
    ap.add_argument("--colour", default=BACKDROP,
                    help=f"the backdrop to sit them on (default {BACKDROP}, "
                         f"which is what the prompt asks for and the keyer removes)")
    a = ap.parse_args()

    colour = parse_colour(a.colour)
    images = sorted(a.src.glob("*.png"))
    if not images:
        raise SystemExit(f"no images under {a.src}")

    for src in images:
        share = flatten(src, a.out / src.name, colour)
        beside = src.with_suffix(".txt")
        if beside.exists():
            (a.out / beside.name).write_text(beside.read_text())
        print(f"  {src.name}  subject {share * 100:4.0f}% of frame")

    print(f"\n{len(images)} flattened onto rgb{colour} -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
