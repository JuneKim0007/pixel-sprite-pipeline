#!/usr/bin/env python3
"""
Turn "pixel-ish" diffusion output into true pixel art.

SDXL + a pixel-art LoRA renders a 1024x1024 image that *looks* like pixel art:
blocks of roughly N x N screen pixels. But it has three defects that stop it
from being real pixel art:

  1. The block grid is not aligned to any exact lattice, and its phase drifts.
  2. Block edges are anti-aliased, so "one pixel" is really a soft gradient.
  3. It uses thousands of distinct colours, not a bounded palette.

This script fixes each defect in order:

  1. align   -- search every (x, y) phase offset and pick the one that makes
                blocks most internally uniform (minimum intra-block variance).
  2. reduce  -- collapse each block to a single colour (median / mode / mean).
  3. quantize-- clamp to a bounded palette (median-cut, or a supplied one).

Optionally lifts a flat background to alpha, and re-upscales with nearest
neighbour so the result is viewable without a pixel-art viewer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# ---------------------------------------------------------------- grid phase


def _blocks(arr: np.ndarray, factor: int, ox: int, oy: int) -> np.ndarray:
    """Crop to the phase (ox, oy) and reshape into (bh, bw, factor, factor, C)."""
    h, w = arr.shape[:2]
    bh = (h - oy) // factor
    bw = (w - ox) // factor
    if bh < 1 or bw < 1:
        raise ValueError(f"factor {factor} too large for image {w}x{h}")
    cropped = arr[oy : oy + bh * factor, ox : ox + bw * factor]
    c = arr.shape[2]
    return cropped.reshape(bh, factor, bw, factor, c).swapaxes(1, 2)


def find_phase(arr: np.ndarray, factor: int) -> tuple[int, int]:
    """Find the (ox, oy) offset whose blocks are most internally uniform.

    The generated pixel grid rarely starts exactly at (0, 0). Sampling on the
    wrong phase straddles block boundaries and smears two logical pixels into
    one, which is the single biggest cause of muddy output.
    """
    best, best_cost = (0, 0), float("inf")
    for oy in range(factor):
        for ox in range(factor):
            blocks = _blocks(arr.astype(np.float32), factor, ox, oy)
            # variance within each block, summed over all blocks and channels
            cost = float(blocks.var(axis=(2, 3)).sum())
            if cost < best_cost:
                best, best_cost = (ox, oy), cost
    return best


# ------------------------------------------------------------ block reduce


# Per-channel standard deviation inside a block, above which the block is
# treated as containing a feature rather than a surface. 34 was chosen by
# sweeping a generated canonical: below about 25 it starts firing on ordinary
# cloth shading, above about 45 it stops catching eyes.
SALIENT_THRESHOLD = 34.0


# Fraction of a block that must survive clipping for the clipped mean to be
# trusted. Below it the block is mostly outliers, which is what a thin line
# through an otherwise flat block looks like - so those fall back to median,
# which is the mode that keeps outlines.
_CLIP_FLOOR = 0.35


def reduce_blocks(arr: np.ndarray, factor: int, ox: int, oy: int, how: str,
                  tolerance: float = 32.0) -> np.ndarray:
    blocks = _blocks(arr, factor, ox, oy)
    bh, bw, _, _, c = blocks.shape
    flat = blocks.reshape(bh, bw, factor * factor, c)

    if how == "mean":
        return flat.mean(axis=2).round().astype(np.uint8)

    if how == "median":
        return np.median(flat, axis=2).round().astype(np.uint8)

    if how == "mode":
        # Most frequently occurring exact colour in the block. Best when the
        # input is already strongly posterised; degenerates to noise otherwise.
        out = np.empty((bh, bw, c), dtype=np.uint8)
        packed = (
            flat[..., 0].astype(np.uint32) << 16
            | flat[..., 1].astype(np.uint32) << 8
            | flat[..., 2].astype(np.uint32)
        )
        for y in range(bh):
            for x in range(bw):
                vals, counts = np.unique(packed[y, x], return_counts=True)
                win = int(vals[counts.argmax()])
                out[y, x, 0] = (win >> 16) & 0xFF
                out[y, x, 1] = (win >> 8) & 0xFF
                out[y, x, 2] = win & 0xFF
                if c == 4:
                    out[y, x, 3] = int(np.median(flat[y, x, :, 3]))
        return out

    if how == "clipped":
        # Two-pass clipped mean: take the block's mean, discard the pixels that
        # sit further than `tolerance` from it, then re-mean what survives.
        #
        # The point is which pixels get a vote. A plain mean lets one bright
        # specular pixel drag the whole block; median ignores magnitude
        # entirely. Clipping throws out the outliers and then lets the
        # remaining pixels average properly, so a block that is 90% one colour
        # returns that colour cleanly rather than a colour pulled toward the
        # 10%.
        #
        # Distance is in RGB, and the tolerance is that distance rather than a
        # multiple of the block's own spread: a fixed number means the same
        # setting behaves the same on a flat block and a busy one, which is
        # what makes it tunable by eye.
        #
        # The known limit, and it is real: a thin line that legitimately
        # separates two regions is a minority inside its block, so it is
        # exactly what gets clipped away. Blocks whose survivors fall below
        # `_CLIP_FLOOR` of the block are therefore left to the median, which
        # keeps thin dark outlines that this pass would otherwise eat.
        rgb = flat[..., :3].astype(np.float32)
        mean = rgb.mean(axis=2, keepdims=True)
        dist = np.sqrt(((rgb - mean) ** 2).sum(axis=3, keepdims=True))
        keep = dist <= max(float(tolerance), 0.0)

        kept = keep.sum(axis=2, keepdims=True)
        enough = kept >= max(1, int(factor * factor * _CLIP_FLOOR))
        safe = np.where(kept > 0, kept, 1)
        clipped = (rgb * keep).sum(axis=2, keepdims=True) / safe

        out_rgb = np.where(enough, clipped, np.median(rgb, axis=2, keepdims=True))
        out = np.empty((bh, bw, c), dtype=np.uint8)
        out[..., :3] = out_rgb[:, :, 0, :].round().clip(0, 255).astype(np.uint8)
        if c == 4:
            # Alpha is a mask, not a colour: averaging it feathers the edge the
            # keyer is about to cut. Median keeps it binary.
            out[..., 3] = np.median(flat[..., 3], axis=2).round().astype(np.uint8)
        return out

    if how == "salient":
        # Median everywhere except where a block has real contrast in it, and
        # there keep the pixel furthest from the median instead.
        #
        # This exists for eyes. At a 128px sprite a face is about eighteen
        # pixels and an eye is two or three; the model draws that eye at 1024
        # as an anti-aliased gradient twenty pixels across, and taking the
        # median of an 8x8 block containing a dark pupil against light skin
        # returns the skin. The pupil does not survive, and neither does any
        # other small dark feature - which includes the outline.
        #
        # Mode does not help: on anti-aliased input almost every pixel is a
        # unique colour, so the most frequent one is arbitrary.
        #
        # Measured on a generated canonical: 3% of blocks exceed the threshold,
        # which is about the share a face and its outlines occupy. Flat cloth
        # and background are untouched, so this is not a global contrast boost.
        med = np.median(flat, axis=2)
        deviation = np.abs(flat - med[:, :, None, :]).sum(axis=3)
        extreme = np.take_along_axis(
            flat, deviation.argmax(axis=2)[:, :, None, None], axis=2)[:, :, 0, :]
        spread = flat.std(axis=2).mean(axis=2)
        contrasty = spread > SALIENT_THRESHOLD
        return np.where(contrasty[..., None], extreme, med).round().astype(np.uint8)

    raise ValueError(f"unknown reduce mode: {how}")


# -------------------------------------------------------------- palette


def load_palette(path: Path) -> list[tuple[int, int, int]]:
    """Read a palette file: one hex colour per line (#rrggbb), '#' comments ok."""
    colours = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        line = line.lstrip("#").strip()
        if len(line) < 6:
            continue
        token = line.split()[0][:6]
        try:
            colours.append(
                (int(token[0:2], 16), int(token[2:4], 16), int(token[4:6], 16))
            )
        except ValueError:
            continue
    if not colours:
        raise ValueError(f"no colours parsed from {path}")
    return colours


def extract_palette(
    rgb: np.ndarray, colours: int, ignore_alpha: np.ndarray | None = None
) -> list[tuple[int, int, int]]:
    """Derive a palette from an image via median cut.

    This is what makes colour consistent across an animation. Rather than
    hoping every frame independently lands on similar colours — it won't,
    each is a separate sample — extract the palette once from the canonical
    sprite and snap every frame to that same fixed set. Consistency stops
    being probabilistic and becomes exact.
    """
    pixels = rgb.reshape(-1, 3)
    if ignore_alpha is not None:
        pixels = pixels[ignore_alpha.reshape(-1) > 0]
    if len(pixels) == 0:
        raise ValueError("no opaque pixels to extract a palette from")

    strip = Image.fromarray(pixels.reshape(-1, 1, 3).astype(np.uint8), mode="RGB")
    q = strip.quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: colours * 3]
    return [tuple(pal[i : i + 3]) for i in range(0, len(pal), 3)]


def save_palette(palette: list[tuple[int, int, int]], path: Path, note: str = "") -> None:
    lines = [f"// {note}"] if note else []
    lines += [f"{r:02X}{g:02X}{b:02X}" for r, g, b in palette]
    path.write_text("\n".join(lines) + "\n")


# Rec. 709 luminance. Green carries most of perceived brightness, which is
# why plain RGB distance misjudges: it weighs a shift in blue as heavily as
# the same shift in green, and the eye does not.
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

MATCH_METHODS = ("rgb", "weighted", "luma", "lab")


def _to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB to CIELAB. Approximate D65, which is close enough to rank by."""
    a = rgb.astype(np.float32) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]], dtype=np.float32)
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def curves(rgb: np.ndarray, *, brightness: float = 0.0, contrast: float = 1.0,
           gamma: float = 1.0, saturation: float = 1.0) -> np.ndarray:
    """Tonal adjustment, applied before quantisation rather than after.

    Order matters and this is the useful order. Snapping to a palette is a
    nearest-neighbour decision, so what you do to the values beforehand
    decides which entries get chosen: lifting contrast pushes midtones out
    toward the ends of the ramp and the sprite picks up its light and dark
    entries instead of collapsing into the middle ones. The same adjustment
    applied afterwards would just move colours off the palette again.

    gamma is separated from brightness deliberately. Brightness shifts every
    value equally and flattens the ramp; gamma redistributes within it, which
    is what "the shadows are too dark but the highlights are fine" needs.
    """
    a = rgb.astype(np.float32) / 255.0
    if gamma != 1.0:
        a = np.power(np.clip(a, 0.0, 1.0), 1.0 / max(gamma, 1e-3))
    if contrast != 1.0:
        a = (a - 0.5) * contrast + 0.5
    if brightness:
        a = a + brightness
    if saturation != 1.0:
        grey = (a * LUMA).sum(axis=-1, keepdims=True)
        a = grey + (a - grey) * saturation
    return (np.clip(a, 0.0, 1.0) * 255.0).round().astype(np.uint8)


def generate_palette(rgb: np.ndarray, colours: int, *, method: str = "weighted",
                     iterations: int = 12,
                     alpha: np.ndarray | None = None) -> list[tuple[int, int, int]]:
    """Derive an N-colour palette by clustering in the chosen metric's space.

    extract_palette does median cut, which subdivides the RGB cube along its
    widest axis and therefore inherits plain RGB's blind spots — it will spend
    two entries separating blues a person cannot tell apart while merging two
    greens they can.

    Clustering in the same space the snapping will use avoids that: choose
    `lab` and the palette is spaced by perceptual difference, choose `luma`
    and it is spaced along the value ramp, which for a sprite is often exactly
    what you want — a guaranteed spread of lightnesses rather than a
    guaranteed spread of hues.

    k-means, seeded by k-means++ so the result does not depend on which
    pixels happen to come first, and capped at a few iterations because the
    centroids stop moving visibly long before they stop moving.
    """
    pixels = rgb.reshape(-1, 3)
    if alpha is not None:
        pixels = pixels[alpha.reshape(-1) > 0]
    if len(pixels) == 0:
        raise ValueError("no opaque pixels to build a palette from")
    colours = max(1, min(int(colours), len(np.unique(pixels, axis=0))))

    # Cluster in the metric's space, but keep the RGB originals to average.
    space = {
        "lab": lambda p: _to_lab(p.astype(np.float32)),
        "weighted": lambda p: p.astype(np.float32) * LUMA,
        "luma": lambda p: np.concatenate(
            [(p.astype(np.float32) @ LUMA)[:, None], p.astype(np.float32) * 0.1], axis=1),
        "rgb": lambda p: p.astype(np.float32),
    }[method if method in MATCH_METHODS else "weighted"]

    feats = space(pixels)
    rng = np.random.default_rng(0)            # deterministic: same image, same palette

    # k-means++ seeding.
    centres = [int(rng.integers(len(feats)))]
    d2 = ((feats - feats[centres[0]]) ** 2).sum(axis=1)
    for _ in range(colours - 1):
        total = d2.sum()
        if total <= 0:
            centres.append(int(rng.integers(len(feats))))
        else:
            centres.append(int(rng.choice(len(feats), p=d2 / total)))
        d2 = np.minimum(d2, ((feats - feats[centres[-1]]) ** 2).sum(axis=1))

    c = feats[centres].copy()
    labels = np.zeros(len(feats), dtype=np.int64)
    for _ in range(iterations):
        labels = ((feats[:, None, :] - c[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
        moved = False
        for k in range(len(c)):
            members = feats[labels == k]
            if len(members):
                nxt = members.mean(axis=0)
                moved |= not np.allclose(nxt, c[k])
                c[k] = nxt
        if not moved:
            break

    out = []
    for k in range(len(c)):
        members = pixels[labels == k]
        if len(members):
            out.append(tuple(int(v) for v in members.mean(axis=0).round()))
    return out or [tuple(int(v) for v in pixels[0])]


def fit_to_palette(rgb: np.ndarray, palette: list[tuple[int, int, int]],
                   *, method: str = "weighted",
                   alpha: np.ndarray | None = None,
                   strength: float = 1.0) -> np.ndarray:
    """Stretch the image's value range onto the palette's, then snap.

    Nearest-colour matching is absolute, and that is usually right: it keeps
    the colours the picture is actually made of. But it cannot widen anything.
    An image whose subject spans luminance 86 to 170 snapped onto a palette
    spanning 0 to 255 comes back spanning 86 to 170 - measured, using 11 of 32
    entries, two thirds of the palette unused and the sprite reading flat.

    The stretch is along LUMINANCE ONLY, with hue and saturation carried
    through untouched. That is not a simplification, it is the point. A sprite
    reads by its value structure, so the range worth matching to the palette is
    the value range; scaling the channels independently would drift the colours
    as well, and the snap that follows already decides hue.

    The first version of this scaled in the matching metric's own space and
    then applied the result in RGB. The luminance weights differ by a factor
    of ten between green and blue, so a single scalar taken across them was
    meaningless: it made the same test case worse, 5 of 32 entries instead of
    11, and pushed everything to the bright end.

    `alpha` excludes the backdrop from the measurement. A flat magenta key
    would otherwise define one end of the source range, and the stretch would
    be computed against a colour that is about to be deleted.

    `strength` below 1 interpolates back toward the original.
    """
    if not palette:
        return rgb

    pal = np.asarray(palette, dtype=np.float32)
    flat = rgb.reshape(-1, 3).astype(np.float32)
    mask = None if alpha is None else (alpha.reshape(-1) > 0)
    sample = flat[mask] if mask is not None and mask.any() else flat

    src_l = sample @ LUMA
    dst_l = pal @ LUMA
    lo_s, hi_s = float(src_l.min()), float(src_l.max())
    lo_d, hi_d = float(dst_l.min()), float(dst_l.max())
    span = hi_s - lo_s
    if span < 1e-6:
        return apply_fixed_palette(rgb, palette, method=method)

    gain = (hi_d - lo_d) / span
    lum = flat @ LUMA
    target = lo_d + (lum - lo_s) * gain
    if strength < 1.0:
        target = lum + (target - lum) * float(strength)

    # Move each pixel along its own luminance axis, keeping the colour it had.
    # Scaling is right for a dark pixel getting darker and wrong at the top,
    # where a scale would clip the brightest channel and shift the hue; the
    # blend toward white above the source midpoint keeps the hue instead.
    out = np.empty_like(flat)
    safe = np.maximum(lum, 1e-6)[:, None]
    scaled = flat * (target[:, None] / safe)
    over = target > np.maximum(lum, 1e-6)
    headroom = np.clip((255.0 - lum) / np.maximum(255.0 - lum, 1e-6), 0, 1)[:, None]
    lighten = flat + (255.0 - flat) * np.clip(
        ((target - lum) / np.maximum(255.0 - lum, 1e-6))[:, None], 0, 1) * headroom
    out[:] = np.where(over[:, None], lighten, scaled)

    out = np.clip(out, 0.0, 255.0).astype(np.uint8)
    return apply_fixed_palette(out.reshape(rgb.shape), palette, method=method)


def apply_fixed_palette(
    rgb: np.ndarray,
    palette: list[tuple[int, int, int]],
    method: str = "weighted",
) -> np.ndarray:
    """Snap every pixel to its nearest palette entry.

    "Nearest" is a choice, not a fact, and for sprites it is a consequential
    one. Four metrics, in order of how much they cost:

    rgb        Plain euclidean in RGB. Fast, and perceptually the worst — it
               treats a shift in blue as equal to the same shift in green.
               Kept because it is what every earlier output used.

    weighted   Euclidean with the luminance weights applied per channel. Costs
               nothing extra and fixes most of what rgb gets wrong.

    luma       Match on brightness first, hue only to break ties. This is the
               one built for pixel art specifically: a sprite reads by its
               value structure, and snapping a mid-tone to an entry of the
               wrong lightness collapses the form even when the hue is right.
               Use it when remapping between palettes that do not share a
               colour scheme.

    lab        Euclidean in CIELAB — perceptually near-uniform, so it picks
               the colour a person would call closest. The most faithful for
               recolouring, and the slowest by roughly an order of magnitude.

    Every metric is a nearest-neighbour search over the palette, so cost grows
    with pixels x palette entries. A 128x128 sprite against 136 entries is two
    million comparisons: nothing. A 1024x1024 frame is 140 million, which is
    why this runs after reduction rather than before.
    """
    if method not in MATCH_METHODS:
        raise ValueError(
            f"unknown palette match method '{method}'; expected one of {MATCH_METHODS}")

    pal = np.asarray(palette, dtype=np.float32)

    # Snap each DISTINCT colour once and index the result, rather than walking
    # every pixel past every entry.
    #
    # The cost of a nearest-colour search is pixels x entries, and a 1280px
    # image against a 136-entry palette is 223 million distance computations,
    # measured at 3.7 s. The number of distinct colours is far smaller than the
    # number of pixels - and smaller again after block reduction, which is
    # where this usually runs - so doing the search per colour is 3 to 4x
    # faster and, unlike a quantised lookup grid, produces exactly the same
    # image. A 32-cubed grid measured 137x faster and agreed with the direct
    # answer on only 95.6% of pixels, which is the wrong trade for a step whose
    # whole job is making colour exact.
    source = rgb.reshape(-1, 3)
    uniq, inverse = np.unique(source, axis=0, return_inverse=True)
    flat = uniq.astype(np.float32)

    if method == "lab":
        a, b = _to_lab(flat), _to_lab(pal)
    elif method == "weighted":
        a, b = flat * LUMA, pal * LUMA
    elif method == "luma":
        # Lightness dominates; chroma is a tiebreaker at a tenth of the
        # weight, which is enough to choose between two entries of equal
        # brightness without letting hue override the value ramp.
        a = np.concatenate([(flat @ LUMA)[:, None], flat * 0.1], axis=1)
        b = np.concatenate([(pal @ LUMA)[:, None], pal * 0.1], axis=1)
    else:
        a, b = flat, pal

    # (N, 1, K) - (1, P, K) -> (N, P), where N is now colours, not pixels.
    dist = ((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)
    idx = dist.argmin(axis=1)
    return pal[idx].astype(np.uint8)[inverse].reshape(rgb.shape)


def quantize_median_cut(rgb: np.ndarray, colours: int, dither: bool) -> np.ndarray:
    img = Image.fromarray(rgb, mode="RGB")
    d = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    q = img.quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=d)
    return np.asarray(q.convert("RGB"))


# ----------------------------------------------------------- background


def background_to_alpha(rgb: np.ndarray, tol: int, passes: int = 3,
                        keep_min: float = 0.04,
                        key: tuple[int, int, int] | None = None) -> np.ndarray:
    """Flood fill from the edges; matching regions become transparent.

    Only removes background *connected to an edge*, so an enclosed region of
    the same colour inside the sprite is preserved.

    Repeats up to `passes` times, because one pass only removes the single
    colour the corners happen to sit on. Generated sprites often arrive on a
    two-tone backdrop — a coloured panel inside a border — and a single pass
    leaves the panel counted as subject. Measured on a real canonical: 39% of
    the canvas survived as "subject", which also polluted the palette, since
    the palette stage extracts its colours from this same mask.

    A pass is discarded if it would leave less than `keep_min` of the canvas,
    so a sprite that genuinely fills the frame is never eaten.
    """
    h, w = rgb.shape[:2]
    alpha = np.full((h, w), 255, dtype=np.uint8)

    # A named backdrop is removed by colour rather than by connectivity. The
    # flood below only reaches background touching an edge, which is right when
    # you are guessing; when the colour was specified in the prompt, a patch of
    # it showing through a gap in the character is also background, and leaving
    # it opaque puts a magenta hole in the sprite.
    if key is not None:
        want = np.asarray(key, dtype=np.int16)
        near = (np.abs(rgb[..., :3].astype(np.int16) - want).max(axis=2) <= tol * 3)
        if near.mean() < 1.0 - keep_min:
            alpha[near] = 0

    for _ in range(max(1, passes)):
        # Seed from whatever is still opaque on the border. After the first
        # pass that is the next background layer inward, not the original edge.
        seeds = []
        for x in range(w):
            for y in (0, h - 1):
                if alpha[y, x]:
                    seeds.append((y, x))
        for y in range(h):
            for x in (0, w - 1):
                if alpha[y, x]:
                    seeds.append((y, x))
        if not seeds:
            break

        seen = np.zeros((h, w), dtype=bool)
        trial = alpha.copy()
        stack = []
        for y, x in seeds:
            if not seen[y, x]:
                seen[y, x] = True
                stack.append((y, x, rgb[y, x].astype(np.int16)))

        while stack:
            y, x, ref = stack.pop()
            trial[y, x] = 0
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and not seen[ny, nx] and trial[ny, nx]:
                    if int(np.abs(rgb[ny, nx].astype(np.int16) - ref).max()) <= tol:
                        seen[ny, nx] = True
                        stack.append((ny, nx, ref))

        remaining = (trial > 0).mean()
        if remaining < keep_min:
            break            # this pass would have eaten the subject
        if (trial > 0).sum() == (alpha > 0).sum():
            break            # nothing more to remove
        alpha = trial

    return np.dstack([rgb, alpha])


# ---------------------------------------------------------------- driver


def pixelize(
    src: Path,
    dst: Path,
    factor: int,
    reduce: str,
    colours: int,
    palette: list[tuple[int, int, int]] | None,
    dither: bool,
    match: str,
    alpha_tol: int | None,
    upscale: int,
    phase: tuple[int, int] | None,
    verbose: bool,
    tolerance: float = 32.0,
    key: tuple[int, int, int] | None = None,
) -> None:
    img = Image.open(src).convert("RGB")
    arr = np.asarray(img)

    ox, oy = phase if phase is not None else find_phase(arr, factor)
    if verbose:
        print(f"  grid phase: ({ox}, {oy})")

    small = reduce_blocks(arr, factor, ox, oy, reduce, tolerance)
    if verbose:
        print(f"  reduced: {img.width}x{img.height} -> {small.shape[1]}x{small.shape[0]}")

    if palette is not None:
        small = apply_fixed_palette(small, palette, method=match)
        if verbose:
            print(f"  snapped to fixed palette ({len(palette)} colours)")
    elif colours > 0:
        small = quantize_median_cut(small, colours, dither)
        if verbose:
            print(f"  quantised to {colours} colours (dither={dither})")

    if alpha_tol is not None:
        small = background_to_alpha(small, alpha_tol, key=key)
        if verbose:
            opaque = int((small[..., 3] > 0).sum())
            print(f"  background keyed: {opaque}/{small[..., 3].size} px opaque")

    out = Image.fromarray(small)
    if upscale > 1:
        out = out.resize(
            (out.width * upscale, out.height * upscale), Image.Resampling.NEAREST
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst)
    print(f"{src.name} -> {dst}  ({out.width}x{out.height})")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Convert pixel-ish diffusion output into true pixel art.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  pixelize.py out/hero.png -f 8 -c 32\n"
            "  pixelize.py out/*.png -o sprites/ -f 8 -c 24 --alpha 12 --upscale 4\n"
            "  pixelize.py out/hero.png --palette palettes/pico8.hex\n"
        ),
    )
    p.add_argument("inputs", nargs="+", type=Path, help="input image(s)")
    p.add_argument("-o", "--outdir", type=Path, help="output directory")
    p.add_argument(
        "-f", "--factor", type=int, default=8,
        help="downscale factor; 1024/8 = 128px sprite (default: 8)",
    )
    p.add_argument(
        "-r", "--reduce", choices=("median", "salient", "clipped", "mode", "mean"),
        default="median",
        help="how to collapse each block to one colour (default: median)",
    )
    p.add_argument(
        "--clip-tolerance", type=float, default=32.0, metavar="D",
        help="for --reduce clipped: RGB distance from the block mean beyond "
             "which a pixel is dropped before re-averaging (default: 32)",
    )
    p.add_argument(
        "-c", "--colors", "--colours", dest="colors", type=int, default=32,
        help="palette size via median-cut; 0 disables (default: 32)",
    )
    p.add_argument("--palette", type=Path, help="fixed palette file (hex per line)")
    p.add_argument("--dither", action="store_true", help="Floyd-Steinberg dithering")
    p.add_argument(
        "--match", default="weighted", choices=list(MATCH_METHODS),
        help="how 'nearest colour' is decided when snapping to a fixed palette: "
             "rgb (fast, perceptually worst), weighted (default), luma "
             "(preserves the value ramp — best for remapping between unrelated "
             "palettes), lab (most faithful, slowest)")
    p.add_argument(
        "--alpha", type=int, metavar="TOL", nargs="?", const=10, default=None,
        help="key out edge-connected background, tolerance 0-255 (default 10)",
    )
    p.add_argument(
        "-u", "--upscale", type=int, default=1,
        help="nearest-neighbour upscale of the result for viewing (default: 1)",
    )
    p.add_argument(
        "--phase", type=str, metavar="X,Y",
        help="force grid phase instead of auto-detecting (e.g. 0,0)",
    )
    p.add_argument("-q", "--quiet", action="store_true")
    a = p.parse_args()

    phase = None
    if a.phase:
        try:
            px, py = (int(v) for v in a.phase.split(","))
            phase = (px, py)
        except ValueError:
            p.error("--phase must look like X,Y")

    palette = load_palette(a.palette) if a.palette else None

    files = [f for f in a.inputs if f.is_file()]
    if not files:
        print("no input files found", file=sys.stderr)
        return 1

    for src in files:
        if a.outdir:
            dst = a.outdir / f"{src.stem}_px.png"
        else:
            dst = src.with_name(f"{src.stem}_px.png")
        if not a.quiet:
            print(src.name)
        pixelize(
            src, dst, a.factor, a.reduce, a.colors, palette, a.dither,
            a.match, a.alpha, a.upscale, phase, not a.quiet, a.clip_tolerance,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
