"""How close a generated sprite is to the reference it was drawn from."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CLIP_MODEL = ROOT / "ComfyUI/models/clip_vision/CLIP-ViT-H-14.safetensors"
_MODEL = None


def _clip():
    global _MODEL
    if _MODEL is None:
        sys.path.insert(0, str(ROOT / "ComfyUI"))
        from comfy.clip_vision import load

        _MODEL = load(str(CLIP_MODEL))
    return _MODEL


def _embed(image: Path):
    import torch

    with Image.open(image) as handle:
        pixels = np.asarray(handle.convert("RGB").resize((224, 224))).astype(np.float32)
    tensor = torch.from_numpy(pixels / 255.0)[None]
    out = _clip().encode_image(tensor)
    vector = out["image_embeds"] if isinstance(out, dict) else out.image_embeds
    return torch.nn.functional.normalize(vector.detach().float().flatten()[None], dim=-1)


def likeness(reference: Path, generated: Path) -> float:
    """Cosine of the CLIP vision embeddings - the encoder IPAdapter itself uses."""
    return float(_embed(reference) @ _embed(generated).T)


def subject(image: Path, tolerance: int = 60) -> tuple[np.ndarray, np.ndarray]:
    from pipeline.geometry.framing import backdrop_of

    with Image.open(image) as handle:
        pixels = np.asarray(handle.convert("RGB")).astype(int)
    bg = backdrop_of(pixels)
    return pixels, np.abs(pixels - bg).sum(axis=2) > tolerance


def bleed(image: Path, key: tuple[int, int, int], within: int = 110) -> float:
    """The share of the SUBJECT wearing the backdrop's colour."""
    pixels, mask = subject(image)
    if not mask.any():
        return 0.0
    body = pixels[mask]
    near = np.abs(body - np.array(key)).sum(axis=1) < within
    return float(near.mean())


def build(image: Path) -> float:
    """Height over shoulder width. The reference sheet measures 4.42."""
    _, mask = subject(image)
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return 0.0
    height = ys.max() - ys.min()
    band = mask[ys.min() + int(height * 0.15):ys.min() + int(height * 0.35)]
    widest = max(int(row.sum()) for row in band) if len(band) else 1
    return height / max(widest, 1)


def block(image: Path) -> float:
    """The pixel grid the MODEL drew, before any reduction."""
    import numpy as np
    from PIL import Image

    from pipeline.definitive.pixelize import estimate_block_size

    with Image.open(image) as handle:
        pixels = np.asarray(handle.convert("RGB"))
    try:
        return float(estimate_block_size(pixels))
    except Exception:                                   # noqa: BLE001
        return 0.0


def cells(image: Path) -> int:
    """How many cells the model drew, canvas over block."""
    from PIL import Image

    found = block(image)
    if not found:
        return 0
    with Image.open(image) as handle:
        return int(round(handle.width / found))


def report(reference: Path, generated: Path,
           key: tuple[int, int, int] = (242, 94, 147)) -> dict:
    return {
        "likeness": round(likeness(reference, generated), 4),
        "bleed": round(bleed(generated, key), 4),
        "build": round(build(generated), 2),
        "block": block(generated),
        "cells": cells(generated),
    }


SIDECAR = "score.json"


def score_into(run_dir: Path, reference: Path,
               key: tuple[int, int, int] = (242, 94, 147)) -> dict | None:
    """Write the score into the run's own record, beside artifacts.json."""
    import json

    made = sorted(run_dir.glob("*_canonical/canonical*.png"))
    if not made:
        return None
    out = report(reference, made[0], key)
    out["reference"] = str(reference)
    (run_dir / SIDECAR).write_text(json.dumps(out, indent=1) + "\n")
    return out


def main() -> int:
    import json

    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    key = (242, 94, 147)
    if len(sys.argv) >= 6:
        key = tuple(int(v) for v in sys.argv[3:6])

    target = Path(sys.argv[2])
    if target.is_dir():
        print(json.dumps(score_into(target, Path(sys.argv[1]), key)))
        return 0
    print(json.dumps(report(Path(sys.argv[1]), target, key)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
