
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IMAGES = (".png", ".jpg", ".jpeg", ".webp")


BLOCK_BANDS = (
    (1.0, 1.6, "1px", "detail one pixel wide — native sprite resolution"),
    (1.6, 3.5, "2-3px", "small blocks"),
    (3.5, 6.5, "4-6px", "medium blocks"),
    (6.5, 64.0, "8px+", "large blocks — an upscaled or low-res sprite"),
)


NOISY_COLOURS = 4_000

MIN_DATASET = 20
GOOD_DATASET = 40


@dataclass
class Target:
    """One thing a dataset can teach, and the shape it has to have to teach it."""

    key: str
    label: str
    teaches: str
    count: str
    vary: list[str] = field(default_factory=list)
    hold: list[str] = field(default_factory=list)
    reject: list[str] = field(default_factory=list)
    caption: str = ""
    shared_with: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "teaches": self.teaches,
            "count": self.count, "vary": self.vary, "hold": self.hold,
            "reject": self.reject, "caption": self.caption,
            "shared_with": self.shared_with,
        }


TARGETS = [
    Target(
        key="style",
        label="Style",
        teaches="The rendering vocabulary: outline weight, how many colours a "
                "form is built from, how many shading steps, how edges are "
                "treated, and the density of the pixel grid itself.",
        count=f"{MIN_DATASET} usable images minimum, {GOOD_DATASET} is comfortable. "
              f"Not thousands — a style LoRA saturates early.",
        vary=[
            "Pose above all. Three poses of one character beat three "
            "characters all standing.",
            "Character, so the LoRA learns a look rather than a person.",
            "Background colour — it cancels out and stops being learned.",
            "Palette. A dataset that is all blue teaches blue.",
        ],
        hold=[
            "Feature scale. This is the one thing that must NOT vary: detail "
            "one pixel wide and detail in eight-pixel blocks cannot both "
            "survive the same reduction, and no post-process recovers the "
            "half that was destroyed. Colour is not in this list — palette "
            "snapping is deterministic and pulls it back.",
            "Outline discipline — either everything is outlined or nothing is.",
            "Figure scale in frame, roughly. Full body throughout, or bust "
            "throughout.",
        ],
        reject=[
            "Watermarks, logos, signatures, site URLs. A LoRA learns these "
            "with total reliability and then draws them on every output.",
            "UI chrome — a thumbnail in the corner, a border, a scrollbar.",
            "Contact sheets. A 3×3 grid teaches 'a 3×3 grid'. Split it first.",
            "Near-duplicates, which is what consecutive animation frames are. "
            "They over-weight one character until the LoRA memorises it.",
            "Anything upscaled from smaller. The interpolation is what gets "
            "learned.",
        ],
        caption="Name the pose, the view and the subject; do NOT name the "
                "style. Whatever you write is attributed to your words, and "
                "whatever you leave out is absorbed into the trigger.",
    ),
    Target(
        key="views",
        label="View coverage",
        teaches="How this particular style renders a back, a side and a "
                "three-quarter turn — which is not derivable from front views "
                "no matter how many of them there are.",
        count="A handful. Four or five non-front images inside the style set, "
              "not a separate dataset.",
        vary=["The angle itself: side, three-quarter rear, rear."],
        hold=["Everything else. This is the same style set, filtered."],
        reject=["Turnarounds drawn by a different artist in a different style — "
                "they teach the other artist's look for rear views only, and "
                "the sprite changes style when it turns around."],
        caption="Always name the view. It is the thing you want to control.",
    ),
    Target(
        key="animation",
        label="Animation",
        teaches="Nothing that needs its own dataset.",
        count="None. Reuse the style set unchanged.",
        vary=[],
        hold=[],
        reject=[
            "Consecutive frames of one character. They are near-duplicates, "
            "and adding them makes the LoRA memorise that character instead "
            "of the style — the opposite of what an animation needs.",
        ],
        caption="",
        shared_with="style",
    ),
]


def target(key: str) -> Target:
    for t in TARGETS:
        if t.key == key:
            return t
    raise KeyError(key)


def _band(block: float) -> tuple[str, str]:
    for low, high, name, why in BLOCK_BANDS:
        if low <= block < high:
            return name, why
    return "8px+", "large blocks — an upscaled or low-res sprite"


def estimate_block_size(arr, candidates: tuple[int, ...] = (1, 2, 3, 4, 6, 8, 12, 16)) -> float:
    """A sprite drawn in eight-pixel blocks loses nothing when averaged in eight-pixel blocks, so its reconstruction error at factor 8 is near zero and rises sharply at 12."""
    import numpy as np

    a = arr.astype(np.float32)
    h, w = a.shape[:2]
    baseline = float(a.var()) or 1.0
    best = 1.0

    for factor in candidates:
        if factor == 1 or h < factor * 8 or w < factor * 8:
            continue
        bh, bw = h // factor, w // factor
        cropped = a[:bh * factor, :bw * factor]
        blocks = cropped.reshape(bh, factor, bw, factor, -1).mean(axis=(1, 3))
        restored = np.repeat(np.repeat(blocks, factor, axis=0), factor, axis=1)
        error = float(((cropped - restored) ** 2).mean())
        # 2% of the image's own variance: comfortably above float noise, comfortably below the error of straddling a real block boundary.
        if error < baseline * 0.02:
            best = float(factor)
    return best


def _ahash(image, size: int = 8) -> int:
    """Average hash. Cheap, and sufficient to catch animation frames."""
    small = image.convert("L").resize((size, size))
    pixels = list(small.getdata())
    mean = sum(pixels) / len(pixels)
    bits = 0
    for i, p in enumerate(pixels):
        if p >= mean:
            bits |= 1 << i
    return bits


def inspect(paths: list[Path]) -> list[dict[str, Any]]:
    """Per-image facts, and the warnings that follow from them."""
    import numpy as np
    from PIL import Image

    out: list[dict[str, Any]] = []
    hashes: list[tuple[int, str]] = []

    for path in paths:
        try:
            image = Image.open(path)
        except OSError as e:
            out.append({"name": path.name, "path": str(path),
                        "error": str(e), "warnings": ["unreadable"], "notes": []})
            continue

        rgb = image.convert("RGB")
        arr = np.asarray(rgb)
        colours = int(len(np.unique(arr.reshape(-1, 3), axis=0)))
        block = estimate_block_size(arr)
        band, why = _band(block)
        w, h = image.size

        warnings: list[str] = []
        notes: list[str] = []
        unique_ratio = colours / max(1, w * h)

        if unique_ratio > 0.15:
            notes.append(
                f"{unique_ratio:.0%} of pixels are a unique colour — lossy "
                f"compression, not drawing. Harmless for structure, and the "
                f"palette stage snaps it back, but a lossless original is "
                f"strictly better.")
        elif colours > NOISY_COLOURS:
            notes.append(f"{colours:,} colours — soft shading. Snapping recovers it.")

        if max(w, h) / max(1, min(w, h)) > 2.6:
            warnings.append(
                f"aspect {w}x{h} is far from square; it is probably a crop "
                f"with empty space or UI around the figure.")

        edges = np.concatenate([arr[:6].reshape(-1, 3), arr[-6:].reshape(-1, 3),
                                arr[:, :6].reshape(-1, 3), arr[:, -6:].reshape(-1, 3)])
        edge_colours = int(len(np.unique(edges, axis=0)))
        if edge_colours > 60:
            warnings.append(
                f"{edge_colours} colours in the border pixels — check for a "
                f"watermark, logo or UI strip. A LoRA learns those reliably.")

        h_ = _ahash(image)
        for other, name in hashes:
            distance = bin(h_ ^ other).count("1")
            if distance <= 6:
                warnings.append(
                    f"near-duplicate of {name} (hamming {distance}); "
                    f"duplicates over-weight one subject.")
                break
        hashes.append((h_, path.name))

        out.append({
            "name": path.name, "path": str(path),
            "width": w, "height": h, "bytes": path.stat().st_size,
            "colours": colours, "block": block, "band": band, "band_why": why,
            "figure_height": figure_height(arr),
            "warnings": warnings, "notes": notes,
        })
    return out


def assess(images: list[dict[str, Any]]) -> dict[str, Any]:
    """Dataset-level verdict. Individually fine images can still be a bad set."""
    usable = [i for i in images if "error" not in i]
    problems: list[str] = []
    notes: list[str] = []

    if len(usable) < MIN_DATASET:
        problems.append(
            f"{len(usable)} images. Below about {MIN_DATASET} a style LoRA "
            f"overfits and reproduces its inputs instead of generalising — "
            f"which is why five good pictures belong in context, not here.")
    elif len(usable) < GOOD_DATASET:
        notes.append(f"{len(usable)} images: trainable, {GOOD_DATASET} is comfortable.")

    bands = {}
    for image in usable:
        bands.setdefault(image["band"], []).append(image["name"])
    if len(bands) > 1:
        spread = ", ".join(f"{k} ({len(v)})" for k, v in sorted(bands.items()))
        blocks = sorted(i["block"] for i in usable)
        ratio = blocks[-1] / max(blocks[0], 1.0)
        if ratio >= 4:
            problems.append(
                f"feature scale spans {spread} — a {ratio:.0f}x range. One "
                f"reduction cannot preserve both: at factor {int(blocks[-1])} "
                f"the fine images lose their detail entirely. Split these into "
                f"separate style sheets, or re-scale the odd ones out.")
        else:
            notes.append(
                f"feature scale varies a little ({spread}). Within about 4x "
                f"one factor still covers them; watch the fine end.")

    if usable:
        counts = sorted(i["colours"] for i in usable)
        if counts[-1] > 8 * max(counts[0], 1):
            notes.append(
                f"colour counts span {counts[0]:,} to {counts[-1]:,}, which is "
                f"cosmetic — the palette stage snaps every output to the "
                f"sheet's palette regardless.")

    flagged = [i for i in usable if i["warnings"]]
    if flagged:
        notes.append(f"{len(flagged)} of {len(usable)} images have a warning.")

    return {
        "count": len(usable),
        "ready": not problems and len(usable) >= MIN_DATASET,
        "problems": problems,
        "notes": notes,
        "bands": {k: len(v) for k, v in bands.items()},
    }


def figure_height(arr, tolerance: int = 16) -> int:
    import numpy as np

    from ..definitive.pixelize import background_to_alpha

    keyed = background_to_alpha(arr, tolerance)
    rows = np.where((keyed[..., 3] > 0).any(axis=1))[0]
    return int(rows[-1] - rows[0] + 1) if len(rows) else int(arr.shape[0])


def plan(images: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [i for i in images if "error" not in i and i.get("block")]
    if not usable:
        return {"target_block": 1, "target_height": 0, "steps": []}

    heights = sorted(i["figure_height"] for i in usable if i.get("figure_height"))
    target_height = heights[len(heights) // 2] if heights else 0

    steps = []
    for image in usable:
        block = image["block"]
        actions = []
        if block > 1.5:
            actions.append({
                "kind": "reduce",
                "factor": int(round(block)),
                "why": f"detail is drawn in {int(round(block))}px blocks; "
                       f"reducing by {int(round(block))} makes one logical "
                       f"pixel one image pixel, losing nothing.",
            })
        native = (image.get("figure_height") or 0) / max(block, 1.0)
        if target_height and native:
            ratio = target_height / native
            if ratio < 0.75 or ratio > 1.34:
                actions.append({
                    "kind": "rescale",
                    "factor": round(ratio, 2),
                    "why": f"the figure is {native:.0f}px tall after reduction "
                           f"against a set median of {target_height:.0f}px. "
                           f"Figure scale is learned as part of the style.",
                })
        if actions:
            steps.append({"name": image["name"], "path": image["path"],
                          "actions": actions})

    return {
        "target_block": 1,
        "target_height": target_height,
        "steps": steps,
        "clean": len(usable) - len(steps),
    }


def preview(home: Path) -> dict[str, Any]:
    """Guidance plus a reading of whatever is actually staged for this look."""
    staged = home / "training" / "images"
    paths = sorted(p for p in staged.glob("*") if p.suffix.lower() in IMAGES) \
        if staged.is_dir() else []
    images = inspect(paths)
    return {
        "targets": [t.as_dict() for t in TARGETS],
        "staged": images,
        "verdict": assess(images),
        "plan": plan(images),
        "dir": str(staged),
    }
