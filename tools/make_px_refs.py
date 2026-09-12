#!/usr/bin/env python3
"""Build the pixelised identity references from the supplied character sheets."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.definitive import pixelize as px  # noqa: E402
from pipeline.geometry import framing  # noqa: E402
from pipeline.looks.vocabulary import BACKDROP  # noqa: E402
from pipeline.shared.colour import parse_colour  # noqa: E402

VIEWS = ("front", "side", "side_right", "rear")
# One canvas for every character, so a sweep varies the LoRA and nothing else.
CANVAS = 1024
FIGURE_CELLS = 350
COLOURS = 16
METHOD = "lab"
SHARE = 0.94
CONTRAST = 1.12


def build(char_dir: Path, key: np.ndarray) -> int:
    found = {v: char_dir / f"{v}.png" for v in VIEWS}
    found = {v: p for v, p in found.items() if p.is_file()}
    if not found:
        return 0
    factor = max(1, round(CANVAS / FIGURE_CELLS))
    for view, src in found.items():
        with Image.open(src) as handle:
            art = handle.convert("RGB")
        box = framing.measure(src)
        crop = art.crop((box.left, box.top, box.right + 1, box.bottom + 1))
        edge = (CANVAS // factor) * factor
        seated = framing.seat(crop, (edge, edge), SHARE, box.backdrop,
                              lattice=factor)
        arr = px.curves(np.asarray(seated).astype(np.uint8),
                        contrast=CONTRAST).astype(np.uint8)
        keyed = framing.key_backdrop(arr) == 0
        arr = np.where(keyed[..., None], key, arr)
        ox, oy = px.find_phase(arr, factor)
        small = px.reduce_blocks(arr, factor, ox, oy, "median", 32.0)
        share = px._blocks(keyed[..., None].astype(np.uint8),
                           factor, ox, oy)[..., 0].mean(axis=(2, 3))
        mask = share > 0.5
        # The palette is a sample, so it can afford to skip the 3% of cells that
        # straddle the edge - and a straddling cell is how the key gets a slot.
        pal = px.generate_palette(small, COLOURS, method=METHOD,
                                  alpha=((share == 0.0) * 255).astype(np.uint8))
        fitted = np.where(mask[..., None], key,
                          px.apply_fixed_palette(small, pal, method=METHOD))
        Image.fromarray(np.repeat(np.repeat(fitted, factor, 0), factor, 1)).save(
            char_dir / f"{view}_px.png")
    return factor


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=ROOT / "characters")
    a = ap.parse_args()

    key = np.array(parse_colour(BACKDROP), np.uint8)
    built = 0
    for char_dir in sorted(p for p in a.src.iterdir() if p.is_dir()):
        factor = build(char_dir, key)
        if factor:
            built += 1
            print(f"  {char_dir.name}: factor {factor}")
    print(f"\n{built} character(s) rebuilt onto rgb{tuple(int(v) for v in key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
