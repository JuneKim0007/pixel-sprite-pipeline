#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.definitive import pixelize as px  # noqa: E402
from pipeline.geometry import framing  # noqa: E402

TOLERANCES = (30, 45, 60)
SURVIVED = 0.85
FLOOR_KEEP = 0.05


def clean(path: Path, tolerance: int = framing.KEY_TOLERANCE,
          parts: bool = True) -> np.ndarray:
    with Image.open(path) as handle:
        rgb = np.asarray(handle.convert("RGB"))

    box = framing.measure(rgb)
    room = ((box.box_width * box.box_height) / (rgb.shape[0] * rgb.shape[1])
            if box else 1.0)

    alpha = None
    for step in TOLERANCES:
        trial = framing.key_backdrop(rgb, step)
        kept = float((trial > 0).mean())
        if kept < FLOOR_KEEP:
            break
        alpha = trial
        if kept / max(room, 1e-6) <= SURVIVED:
            break
    if alpha is None:
        alpha = framing.key_backdrop(rgb, tolerance)

    flooded = px.background_to_alpha(np.dstack([rgb, alpha])[..., :3], 14)
    alpha = np.minimum(alpha, flooded[..., 3])
    if parts:
        alpha = framing.largest_parts(alpha, pixels=rgb)
    return np.dstack([rgb, alpha])


def main() -> int:
    import argparse
    import csv

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=ROOT / "training_set")
    ap.add_argument("--out", type=Path, default=ROOT / "training_set" / "keyed")
    ap.add_argument("--tolerance", type=int, default=framing.KEY_TOLERANCE)
    ap.add_argument("--keep-parts", action="store_true",
                    help="leave detached elements in place")
    a = ap.parse_args()

    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for bucket in ("small", "large"):
        folder = a.src / "sorted" / bucket
        if not folder.is_dir():
            continue
        files = sorted(p for p in folder.iterdir()
                       if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
        for i, source in enumerate(files, 1):
            name = f"{bucket}_{i:02d}.png"
            rgba = clean(source, a.tolerance, parts=not a.keep_parts)
            (a.out / bucket).mkdir(parents=True, exist_ok=True)
            Image.fromarray(rgba, "RGBA").save(a.out / bucket / name)
            kept = float((rgba[..., 3] > 0).mean())
            rows.append({"name": name, "bucket": bucket,
                         "source": source.name, "kept": f"{kept:.3f}"})
            print(f"  {name}  {kept:>5.0%} kept   <- {source.name}")

    with open(a.out / "index.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "bucket", "source", "kept"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n{len(rows)} written to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
