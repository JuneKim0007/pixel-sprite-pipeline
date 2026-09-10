"""Cut a character sheet into one image per view.

    python tools/cut_sheet.py ~/Downloads/Char_sheet/char2.png library/refs/char2
    python tools/cut_sheet.py sheet.png out --views 3

A figure is a column whose ink spans most of the height the figures share.
Detail panels, palettes and captions are shorter, so they fall out without
reading a single label - which matters because these sheets put their panel on
the left, on the right, or nowhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.geometry.framing import backdrop_of  # noqa: E402

TOLERANCE = 45
MIN_TALL = 0.4
MAX_WIDE = 0.45
MIN_WIDE = 0.04
BASELINE_SLACK = 0.12
JOIN = 10

# Raised only when a sheet yields too few figures: at 0.55 two figures drawn
# close together merge into one column, and at 0.75 they separate.
SPANS = (0.55, 0.75, 0.85)

THREE = ("front", "side", "rear")
FOUR = ("front", "side", "side_right", "rear")


def _mask(pixels: np.ndarray) -> np.ndarray:
    return np.abs(pixels - backdrop_of(pixels)).sum(axis=2) > TOLERANCE


def _band(mask: np.ndarray) -> tuple[int, int]:
    """The rows the figures share, found from the blobs that agree on both."""
    from scipy import ndimage

    height, width = mask.shape
    labelled, _ = ndimage.label(ndimage.binary_closing(mask, np.ones((9, 9))))
    boxes = []
    for slab in ndimage.find_objects(labelled):
        left, top = slab[1].start, slab[0].start
        right, bottom = slab[1].stop, slab[0].stop
        if bottom - top < height * MIN_TALL:
            continue
        if not (width * MIN_WIDE < right - left < width * MAX_WIDE):
            continue
        boxes.append((left, top, right, bottom))
    if not boxes:
        raise SystemExit("no figure-shaped blob on this sheet")

    best: list = []
    slack = height * BASELINE_SLACK
    for anchor in boxes:
        group = [b for b in boxes
                 if abs(b[1] - anchor[1]) < slack and abs(b[3] - anchor[3]) < slack]
        if len(group) > len(best):
            best = group
    return min(b[1] for b in best), max(b[3] for b in best)


def _columns(mask: np.ndarray, band: tuple[int, int], span: float) -> list[tuple[int, int]]:
    top, bottom = band
    height = bottom - top
    strip = mask[top:bottom]
    reach = np.zeros(mask.shape[1])
    for x in range(mask.shape[1]):
        rows = np.nonzero(strip[:, x])[0]
        if len(rows):
            reach[x] = (rows[-1] - rows[0]) / height

    runs: list[list[int]] = []
    for x in np.nonzero(reach > span)[0]:
        if runs and x - runs[-1][1] <= JOIN:
            runs[-1][1] = int(x)
        else:
            runs.append([int(x), int(x)])
    return [(a, b) for a, b in runs if b - a > mask.shape[1] * MIN_WIDE]


OVERSIZE = 1.6
MARGIN = 0.16


def _columns_reach(mask: np.ndarray, band: tuple[int, int]) -> np.ndarray:
    top, bottom = band
    strip = mask[top:bottom]
    reach = np.zeros(mask.shape[1])
    for x in range(mask.shape[1]):
        rows = np.nonzero(strip[:, x])[0]
        if len(rows):
            reach[x] = (rows[-1] - rows[0]) / (bottom - top)
    return reach


def _resplit(reach: np.ndarray, picked: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """A column far wider than its siblings has caught a full-height panel.

    Figures on one sheet are drawn at one scale, so a run 1.6x the median is
    two things joined by a frame line. Split it at its thinnest point and keep
    whichever half is the usual width - the panel may sit on either side.
    """
    if len(picked) < 3:
        return picked
    widths = sorted(b - a for a, b in picked)
    median = widths[len(widths) // 2]

    out = []
    for left, right in picked:
        if right - left <= median * OVERSIZE:
            out.append((left, right))
            continue
        inner = reach[left:right]
        margin = max(1, int((right - left) * 0.2))
        cut_at = left + margin + int(np.argmin(inner[margin:-margin]))
        halves = [(left, cut_at), (cut_at, right)]
        out.append(min(halves, key=lambda h: abs((h[1] - h[0]) - median)))
    return out


def _extent(mask: np.ndarray, column: tuple[int, int], band: tuple[int, int],
            bounds: tuple[int, int]) -> tuple[int, int, int, int]:
    """The column plus a margin for the arms, trimmed back to real ink.

    Measuring a blob instead was tried and is worse: closed loosely a sleeve
    detaches, closed tightly the panel beside the figure joins it, and the
    sheets carry both a caption inside the figure's rows and a palette strip
    beside them.
    """
    left, right = column
    top, bottom = band
    margin = int((right - left) * MARGIN)
    box = (max(bounds[0], left - margin), top,
           min(bounds[1], right + margin), bottom)

    window = mask[box[1]:box[3], box[0]:box[2]]
    cols = np.nonzero(window.any(axis=0))[0]
    rows = np.nonzero(window.any(axis=1))[0]
    if not len(cols) or not len(rows):
        return box
    return (box[0] + int(cols[0]), box[1] + int(rows[0]),
            box[0] + int(cols[-1]), box[1] + int(rows[-1]))


def cut(sheet: Path, outdir: Path, views: int = 0) -> list[str]:
    with Image.open(sheet) as handle:
        image = handle.convert("RGB")
    mask = _mask(np.asarray(image).astype(int))
    band = _band(mask)

    wanted = views or 4
    for span in SPANS:
        found = _columns(mask, band, span)
        if len(found) >= wanted:
            break
    if len(found) < 3:
        raise SystemExit(f"{sheet.name}: found {len(found)} figures, need 3 or 4")
    if not views:
        wanted = 4 if len(found) >= 4 else 3

    picked = _resplit(_columns_reach(mask, band), found[:wanted])
    # The blob pass finds the top reliably and can lose the feet: on a sheet
    # whose legs are pale on a strong ground they break into their own blobs
    # and the baseline lands at the knee. The picked columns know better.
    floor = max(int(np.nonzero(mask[:, a:b].any(axis=1))[0][-1]) for a, b in picked)
    band = (band[0], max(band[1], floor) + 1)
    names = FOUR if wanted == 4 else THREE
    outdir.mkdir(parents=True, exist_ok=True)
    image.save(outdir / "_source_sheet.png")

    written = []
    for i, (left, right) in enumerate(picked):
        lo = 0 if i == 0 else (found[i - 1][1] + left) // 2
        hi = image.width if i + 1 >= len(found) else (right + found[i + 1][0]) // 2
        box = _extent(mask, (left, right), band, (lo, hi))
        image.crop((box[0], box[1], box[2] + 1, box[3] + 1)).save(
            outdir / f"{names[i]}.png")
        written.append(f"{names[i]:11} {box[2] - box[0]}x{box[3] - box[1]}")

    # One side drawing answers for both, rather than a missing reference.
    if wanted == 3:
        Image.open(outdir / "side.png").save(outdir / "side_right.png")
        written.append("side_right  copied from side")
    return written


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    views = 0
    if "--views" in sys.argv:
        views = int(sys.argv[sys.argv.index("--views") + 1])
        args = [a for a in args if a != str(views)]
    if len(args) != 2:
        print(__doc__)
        return 2
    for line in cut(Path(args[0]).expanduser(), Path(args[1]), views):
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
