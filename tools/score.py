"""How close a generated sprite is to the reference it was drawn from.

Three numbers, kept apart because they fail independently: a sprite can match
the reference's colours and be the wrong shape, or be the right shape in the
backdrop's colours.
"""

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
    """The share of the SUBJECT wearing the backdrop's colour.

    A chroma key that reaches the character is the failure this measures: the
    reference's black hair came back lavender and its red tie came back
    magenta, and every pixel of that is inside the silhouette.
    """
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


def report(reference: Path, generated: Path,
           key: tuple[int, int, int] = (242, 94, 147)) -> dict:
    return {
        "likeness": round(likeness(reference, generated), 4),
        "bleed": round(bleed(generated, key), 4),
        "build": round(build(generated), 2),
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    print(report(Path(sys.argv[1]), Path(sys.argv[2])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
