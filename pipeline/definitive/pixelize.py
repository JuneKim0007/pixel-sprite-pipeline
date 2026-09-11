#!/usr/bin/env python3


from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ..shared.errors import Invalid, NotFound

REDUCE_MODES = ("mean", "median", "mode", "clipped", "salient")


def _blocks(arr: np.ndarray, factor: int, ox: int, oy: int) -> np.ndarray:
    """Crop to the phase (ox, oy) and reshape into (bh, bw, factor, factor, C)."""
    h, w = arr.shape[:2]
    # Refused on WHOLE blocks: a factor bigger than the image would otherwise pass as one.
    if (h - oy) // factor < 1 or (w - ox) // factor < 1:
        raise Invalid(f"factor {factor} too large for image {w}x{h}", field="factor")
    bh = -(-(h - oy) // factor)
    bw = -(-(w - ox) // factor)
    cropped = arr[oy:, ox:]
    pad_y = bh * factor - cropped.shape[0]
    pad_x = bw * factor - cropped.shape[1]
    if pad_y or pad_x:
        cropped = np.pad(cropped, ((0, pad_y), (0, pad_x), (0, 0)), mode="edge")
    c = arr.shape[2]
    return cropped.reshape(bh, factor, bw, factor, c).swapaxes(1, 2)


def _running_totals(x: np.ndarray, into: np.ndarray) -> np.ndarray:
    """Running totals with a zero row and column, so any rectangle is four lookups."""
    into[1:, 1:] = x
    np.cumsum(into[1:, 1:], axis=0, out=into[1:, 1:])
    np.cumsum(into[1:, 1:], axis=1, out=into[1:, 1:])
    return into


def _phases(height: int, width: int, factor: int):
    """Every origin worth trying, and how many whole blocks it leaves."""
    for oy in range(factor):
        rows = (height - oy) // factor
        if rows < 1:
            continue
        for ox in range(factor):
            columns = (width - ox) // factor
            if columns < 1:
                continue
            yield oy, ox, rows, columns
    # Scoring stays on whole blocks: a padded tail is the same for every phase and would.


def _rects(table: np.ndarray, top: np.ndarray, bottom: np.ndarray,
           left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Every block's total at once, as a (rows, columns, channels) grid."""
    return (table[np.ix_(bottom, right)] - table[np.ix_(top, right)]
            - table[np.ix_(bottom, left)] + table[np.ix_(top, left)])


def find_phase(arr: np.ndarray, factor: int) -> tuple[int, int]:
    """Where the lattice starts: the origin whose blocks are most uniform."""
    _blocks(arr, factor, 0, 0)      # the size refusal, on the same terms as before
    height, width, channels = arr.shape
    table = np.zeros((height + 1, width + 1, channels), dtype=np.int64)

    _running_totals(np.multiply(arr, arr, dtype=np.uint16), table)
    spread = {}
    for oy, ox, rows, columns in _phases(height, width, factor):
        bottom, right = oy + rows * factor, ox + columns * factor
        spread[(oy, ox)] = (table[bottom, right] - table[oy, right]
                            - table[bottom, ox] + table[oy, ox]).astype(np.float64)

    table[:] = 0
    _running_totals(arr, table)
    best, best_cost = (0, 0), float("inf")
    for oy, ox, rows, columns in _phases(height, width, factor):
        top = oy + factor * np.arange(rows)
        left = ox + factor * np.arange(columns)
        block = _rects(table, top, top + factor,
                       left, left + factor).astype(np.float64)
        cost = float((spread[(oy, ox)] / factor ** 2
                      - (block * block).sum(axis=(0, 1)) / factor ** 4).sum())
        if cost < best_cost:
            best, best_cost = (ox, oy), cost
    return best


SALIENT_THRESHOLD = 34.0


_CLIP_FLOOR = 0.35


def estimate_block_size(arr, candidates: tuple[int, ...] = (1, 2, 3, 4, 6, 8, 12, 16)) -> float:
    """Reconstruction error is near zero at the factor the sprite was drawn in."""
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
        # 2% of the image's own variance: above float noise, below a straddled block.
        if error < baseline * 0.02:
            best = float(factor)
    return best


@dataclass(frozen=True)
class _Blocks:
    """One image cut into factor x factor blocks, flattened for reduction."""

    pixels: np.ndarray
    bh: int
    bw: int
    c: int
    factor: int
    tolerance: float

    @classmethod
    def of(cls, arr: np.ndarray, factor: int, ox: int, oy: int,
           tolerance: float) -> "_Blocks":
        blocks = _blocks(arr, factor, ox, oy)
        bh, bw, _, _, c = blocks.shape
        return cls(blocks.reshape(bh, bw, factor * factor, c),
                   bh, bw, c, factor, tolerance)

    def empty(self) -> np.ndarray:
        return np.empty((self.bh, self.bw, self.c), dtype=np.uint8)

    def median_alpha(self) -> np.ndarray:
        return np.median(self.pixels[..., 3], axis=2).round().astype(np.uint8)


def _reduce_mean(b: _Blocks) -> np.ndarray:
    return b.pixels.mean(axis=2).round().astype(np.uint8)


def _reduce_median(b: _Blocks) -> np.ndarray:
    return np.median(b.pixels, axis=2).round().astype(np.uint8)


def _reduce_mode(b: _Blocks) -> np.ndarray:
    """The most frequent exact colour, which needs the block unpacked per pixel."""
    out = b.empty()
    packed = (
        b.pixels[..., 0].astype(np.uint32) << 16
        | b.pixels[..., 1].astype(np.uint32) << 8
        | b.pixels[..., 2].astype(np.uint32)
    )
    for y in range(b.bh):
        for x in range(b.bw):
            vals, counts = np.unique(packed[y, x], return_counts=True)
            win = int(vals[counts.argmax()])
            out[y, x, 0] = (win >> 16) & 0xFF
            out[y, x, 1] = (win >> 8) & 0xFF
            out[y, x, 2] = win & 0xFF
            if b.c == 4:
                out[y, x, 3] = int(np.median(b.pixels[y, x, :, 3]))
    return out


def _reduce_clipped(b: _Blocks) -> np.ndarray:
    # Clipping drops the outliers, so a 90%-one-colour block returns that colour cleanly.
    rgb = b.pixels[..., :3].astype(np.float32)
    mean = rgb.mean(axis=2, keepdims=True)
    dist = np.sqrt(((rgb - mean) ** 2).sum(axis=3, keepdims=True))
    keep = dist <= max(float(b.tolerance), 0.0)

    kept = keep.sum(axis=2, keepdims=True)
    enough = kept >= max(1, int(b.factor * b.factor * _CLIP_FLOOR))
    safe = np.where(kept > 0, kept, 1)
    clipped = (rgb * keep).sum(axis=2, keepdims=True) / safe

    out_rgb = np.where(enough, clipped, np.median(rgb, axis=2, keepdims=True))
    out = b.empty()
    out[..., :3] = out_rgb[:, :, 0, :].round().clip(0, 255).astype(np.uint8)
    if b.c == 4:
        out[..., 3] = b.median_alpha()
    return out


def _reduce_salient(b: _Blocks) -> np.ndarray:
    """A high-contrast block keeps its outlier; a flat one takes its median."""
    med = np.median(b.pixels, axis=2)
    deviation = np.abs(b.pixels - med[:, :, None, :]).sum(axis=3)
    extreme = np.take_along_axis(
        b.pixels, deviation.argmax(axis=2)[:, :, None, None], axis=2)[:, :, 0, :]
    spread = b.pixels.std(axis=2).mean(axis=2)
    contrasty = spread > SALIENT_THRESHOLD
    return np.where(contrasty[..., None], extreme, med).round().astype(np.uint8)


_REDUCERS = {
    "mean": _reduce_mean,
    "median": _reduce_median,
    "mode": _reduce_mode,
    "clipped": _reduce_clipped,
    "salient": _reduce_salient,
}


def reduce_blocks(arr: np.ndarray, factor: int, ox: int, oy: int, how: str,
                  tolerance: float = 32.0) -> np.ndarray:
    blocks = _Blocks.of(arr, factor, ox, oy, tolerance)
    if how not in _REDUCERS:
        raise NotFound("reduce mode", how, available=list(REDUCE_MODES))
    return _REDUCERS[how](blocks)


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
        raise Invalid(f"no colours parsed from {path}", field="file")
    return colours


def extract_palette(
    rgb: np.ndarray, colours: int, ignore_alpha: np.ndarray | None = None
) -> list[tuple[int, int, int]]:

    pixels = rgb.reshape(-1, 3)
    if ignore_alpha is not None:
        pixels = pixels[ignore_alpha.reshape(-1) > 0]
    if len(pixels) == 0:
        raise Invalid("no opaque pixels to extract a palette from")

    strip = Image.fromarray(pixels.reshape(-1, 1, 3).astype(np.uint8), mode="RGB")
    q = strip.quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: colours * 3]
    return [tuple(pal[i : i + 3]) for i in range(0, len(pal), 3)]


def save_palette(palette: list[tuple[int, int, int]], path: Path, note: str = "") -> None:
    lines = [f"// {note}"] if note else []
    lines += [f"{r:02X}{g:02X}{b:02X}" for r, g, b in palette]
    path.write_text("\n".join(lines) + "\n")


LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

MATCH_METHODS = ("rgb", "weighted", "luma", "lab")

_SPACES = {
    "lab": lambda p: _to_lab(p.astype(np.float32)),
    "weighted": lambda p: p.astype(np.float32) * LUMA,
    "luma": lambda p: np.concatenate(
        [(p.astype(np.float32) @ LUMA)[:, None], p.astype(np.float32) * 0.1], axis=1),
    "rgb": lambda p: p.astype(np.float32),
}


def project(colours: np.ndarray, method: str) -> np.ndarray:
    """Colours in the space distances are measured in. Unknown falls to weighted."""
    return _SPACES[method if method in MATCH_METHODS else "weighted"](colours)


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


def palette_chunk(chunk: int | None = None) -> int:
    """Rows of pixels the palette compares against every centre at once."""
    from ..shared import limits

    return max(256, int(chunk or limits.get("colour_chunk")))


def working_bytes(chunk: int, colours: int, width: int = 3) -> int:
    """Peak temporary of one assignment block, in bytes."""
    return chunk * colours * width * 4 + chunk * colours * 4


def _spans(total: int, chunk: int):
    for start in range(0, total, chunk):
        yield start, min(start + chunk, total)


def _distinct(pixels: np.ndarray) -> int:
    """How many colours the image actually has."""
    packed = (pixels[:, 0].astype(np.uint32) << 16
              | pixels[:, 1].astype(np.uint32) << 8
              | pixels[:, 2].astype(np.uint32))
    return int(len(np.unique(packed)))


def _nearest(feats: np.ndarray, centres: np.ndarray, chunk: int):
    """Each row's nearest centre, one bounded block at a time."""
    for start, stop in _spans(len(feats), chunk):
        block = feats[start:stop]
        yield start, stop, ((block[:, None, :] - centres[None, :, :]) ** 2
                            ).sum(axis=2).argmin(axis=1)


def _sq_dist(feats: np.ndarray, point: np.ndarray, chunk: int,
             into: np.ndarray | None = None) -> np.ndarray:
    """Squared distance from every row to one point."""
    out = np.empty(len(feats), dtype=np.float32) if into is None else into
    for start, stop in _spans(len(feats), chunk):
        block = ((feats[start:stop] - point) ** 2).sum(axis=1)
        if into is None:
            out[start:stop] = block
        else:
            np.minimum(out[start:stop], block, out=out[start:stop])
    return out


def _cluster_totals(rows: np.ndarray, feats: np.ndarray, centres: np.ndarray,
                    chunk: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-cluster sums of `rows`, and member counts, in one bounded pass."""
    count, width = len(centres), rows.shape[1]
    sums = np.zeros((count, width), dtype=np.float64)
    members = np.zeros(count, dtype=np.int64)
    for start, stop, labels in _nearest(feats, centres, chunk):
        members += np.bincount(labels, minlength=count)
        block = rows[start:stop]
        for axis in range(width):
            sums[:, axis] += np.bincount(labels, weights=block[:, axis],
                                         minlength=count)
    return sums, members


def generate_palette(rgb: np.ndarray, colours: int, *, method: str = "weighted",
                     iterations: int = 12,
                     alpha: np.ndarray | None = None,
                     chunk: int | None = None) -> list[tuple[int, int, int]]:

    pixels = rgb.reshape(-1, 3)
    if alpha is not None:
        pixels = pixels[alpha.reshape(-1) > 0]
    if len(pixels) == 0:
        raise ValueError("no opaque pixels to build a palette from")
    colours = max(1, min(int(colours), _distinct(pixels)))
    chunk = palette_chunk(chunk)

    feats = project(pixels, method)
    rng = np.random.default_rng(0)

    centres = [int(rng.integers(len(feats)))]
    d2 = _sq_dist(feats, feats[centres[0]], chunk)
    for _ in range(colours - 1):
        total = d2.sum()
        if total <= 0:
            centres.append(int(rng.integers(len(feats))))
        else:
            centres.append(int(rng.choice(len(feats), p=d2 / total)))
        _sq_dist(feats, feats[centres[-1]], chunk, into=d2)

    c = feats[centres].copy()
    assigned = c.copy()
    for _ in range(iterations):
        assigned = c.copy()
        sums, members = _cluster_totals(feats, feats, assigned, chunk)
        moved = False
        for k in range(len(c)):
            if members[k]:
                nxt = (sums[k] / members[k]).astype(np.float32)
                moved |= not np.allclose(nxt, c[k])
                c[k] = nxt
        if not moved:
            break

    sums, members = _cluster_totals(pixels, feats, assigned, chunk)
    out = [tuple(int(v) for v in (sums[k] / members[k]).round())
           for k in range(len(c)) if members[k]]
    return out or [tuple(int(v) for v in pixels[0])]


BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
ANCHOR_NEAR = 16
ANCHOR_SHARE = 0.005


def _uses(pixels: np.ndarray, corner: tuple[int, int, int], near: int) -> float:
    """Share of the art within `near` of a corner of the cube, per channel."""
    if corner == BLACK:
        return float((pixels.max(axis=1) <= near).mean())
    return float((pixels.min(axis=1) >= 255 - near).mean())


def ramp_palette(rgb: np.ndarray, colours: int, *,
                 alpha: np.ndarray | None = None,
                 method: str = "weighted", ramps: int = 0,
                 chunk: int | None = None) -> list[tuple[int, int, int]]:
    """Colour families first, then shades within each, instead of one flat k-means.

    Plain clustering minimises within-cluster variance weighted by pixel count,
    so it spends entries where pixels are DENSE rather than where they are
    distinct - several near-identical tones for the largest garment and none
    left for an accent. Grouping by chroma first and taking shades inside each
    group is how a pixel artist builds a ramp, and it gives every family its
    own value range.
    """
    pixels = rgb.reshape(-1, 3)
    if alpha is not None:
        pixels = pixels[alpha.reshape(-1) > 0]
    if len(pixels) == 0:
        raise ValueError("no opaque pixels to build a palette from")

    colours = max(1, min(int(colours), _distinct(pixels)))
    if ramps <= 0:
        ramps = max(2, int(round(colours ** 0.5)))
    ramps = max(1, min(ramps, colours))

    lum = pixels.astype(np.float32) @ LUMA
    chroma = pixels.astype(np.float32) - lum[:, None]

    families = generate_palette(
        (chroma + 128.0).clip(0, 255).astype(np.uint8).reshape(-1, 1, 3),
        ramps, method="rgb", chunk=chunk)
    centres = np.asarray(families, np.float32) - 128.0
    which = np.abs(chroma[:, None, :] - centres[None, :, :]).sum(axis=2).argmin(axis=1)

    shares = np.array([(which == k).sum() for k in range(len(centres))], np.float64)
    budget = _shared_out(shares, colours)

    out: list[tuple[int, int, int]] = []
    for k, want in enumerate(budget):
        member = pixels[which == k]
        if want <= 0 or not len(member):
            continue
        value = member.astype(np.float32) @ LUMA
        edges = np.quantile(value, np.linspace(0.0, 1.0, want + 1))
        for i in range(want):
            lo, hi = edges[i], edges[i + 1]
            band = member[(value >= lo) & (value <= hi)]
            if len(band):
                out.append(tuple(int(v) for v in band.mean(axis=0).round()))
    return out or [tuple(int(v) for v in pixels[0])]


def _shared_out(shares: np.ndarray, total: int) -> list[int]:
    """Every family gets at least one entry; the rest go by how much art it covers."""
    live = shares > 0
    n = int(live.sum())
    if n == 0:
        return [0] * len(shares)
    out = np.where(live, 1, 0)
    spare = total - n
    if spare > 0:
        weight = np.where(live, shares, 0.0)
        extra = np.floor(weight / weight.sum() * spare).astype(int)
        out = out + extra
        for k in np.argsort(-weight)[:spare - int(extra.sum())]:
            out[k] += 1
    return [int(v) for v in out]


def anchored_palette(rgb: np.ndarray, colours: int, *,
                     alpha: np.ndarray | None = None,
                     method: str = "weighted",
                     keep_black: bool = False, keep_white: bool = False,
                     near: int = ANCHOR_NEAR,
                     min_share: float = ANCHOR_SHARE) -> list[tuple[int, int, int]]:
    """A palette with pure black and white pinned, when the art actually uses them.

    k-means returns cluster means, so an outline drawn in black comes back as
    the average of the outline and whatever it was merged with - a dark grey
    shared with the shadow. Pinning the corner keeps the outline an outline.

    Anchored pixels are withheld from the clustering: without that the free
    centres spend one of themselves re-deriving a near-black that is already
    in the palette.
    """
    pixels = rgb.reshape(-1, 3)
    if alpha is not None:
        pixels = pixels[alpha.reshape(-1) > 0]
    if len(pixels) == 0:
        raise ValueError("no opaque pixels to build a palette from")

    fixed: list[tuple[int, int, int]] = []
    for want, corner in ((keep_black, BLACK), (keep_white, WHITE)):
        if want and _uses(pixels, corner, near) >= min_share:
            fixed.append(corner)
    if not fixed:
        return generate_palette(rgb, colours, method=method, alpha=alpha)

    keep = np.ones(len(pixels), dtype=bool)
    for corner in fixed:
        keep &= np.abs(pixels.astype(np.int16)
                       - np.asarray(corner, np.int16)).max(axis=1) > near

    free = colours - len(fixed)
    if free <= 0 or not keep.any():
        return fixed[:colours]
    rest = generate_palette(pixels[keep].reshape(-1, 1, 3), free, method=method)
    return fixed + rest


def _luminance_range(source: np.ndarray, mask: np.ndarray | None,
                     chunk: int) -> tuple[float, float]:
    """The darkest and brightest the subject gets, read a block at a time."""
    lo, hi = float("inf"), float("-inf")
    for start, stop in _spans(len(source), chunk):
        lum = source[start:stop].astype(np.float32) @ LUMA
        if mask is not None:
            lum = lum[mask[start:stop]]
        if len(lum):
            lo, hi = min(lo, float(lum.min())), max(hi, float(lum.max()))
    return lo, hi


def _stretched(block: np.ndarray, lum: np.ndarray,
               target: np.ndarray) -> np.ndarray:
    """One block moved onto its target luminance."""
    floor = np.maximum(lum, 1e-6)
    ceiling = np.maximum(255.0 - lum, 1e-6)
    scaled = block * (target[:, None] / floor[:, None])
    headroom = np.clip((255.0 - lum) / ceiling, 0, 1)[:, None]
    lighten = block + (255.0 - block) * np.clip(
        ((target - lum) / ceiling)[:, None], 0, 1) * headroom
    return np.where((target > floor)[:, None], lighten, scaled)


def fit_to_palette(rgb: np.ndarray, palette: list[tuple[int, int, int]],
                   *, method: str = "weighted",
                   alpha: np.ndarray | None = None,
                   strength: float = 1.0,
                   chunk: int | None = None) -> np.ndarray:

    if not palette:
        return rgb

    pal = np.asarray(palette, dtype=np.float32)
    source = rgb.reshape(-1, 3)
    chunk = palette_chunk(chunk)

    mask = None if alpha is None else (alpha.reshape(-1) > 0)
    if mask is not None and not mask.any():
        mask = None

    lo_s, hi_s = _luminance_range(source, mask, chunk)
    dst_l = pal @ LUMA
    lo_d, hi_d = float(dst_l.min()), float(dst_l.max())
    span = hi_s - lo_s
    if not span >= 1e-6:
        return apply_fixed_palette(rgb, palette, method=method)

    gain = (hi_d - lo_d) / span
    out = np.empty((len(source), 3), dtype=np.uint8)
    for start, stop in _spans(len(source), chunk):
        block = source[start:stop].astype(np.float32)
        lum = block @ LUMA
        target = lo_d + (lum - lo_s) * gain
        if strength < 1.0:
            target = lum + (target - lum) * float(strength)
        out[start:stop] = np.clip(_stretched(block, lum, target),
                                  0.0, 255.0).astype(np.uint8)

    return apply_fixed_palette(out.reshape(rgb.shape), palette, method=method)


def apply_fixed_palette(
    rgb: np.ndarray,
    palette: list[tuple[int, int, int]],
    method: str = "weighted",
) -> np.ndarray:
    if method not in MATCH_METHODS:
        raise NotFound("palette match method", method, available=list(MATCH_METHODS))

    pal = np.asarray(palette, dtype=np.float32)


    source = rgb.reshape(-1, 3)
    uniq, inverse = np.unique(source, axis=0, return_inverse=True)
    flat = uniq.astype(np.float32)

    a, b = project(flat, method), project(pal, method)

    from ..shared import limits

    chunk = max(256, limits.get("colour_chunk"))
    idx = np.empty(len(a), dtype=np.int64)
    for start in range(0, len(a), chunk):
        stop = min(start + chunk, len(a))
        block = ((a[start:stop, None, :] - b[None, :, :]) ** 2).sum(axis=2)
        idx[start:stop] = block.argmin(axis=1)
    return pal[idx].astype(np.uint8)[inverse].reshape(rgb.shape)


def quantize_median_cut(rgb: np.ndarray, colours: int, dither: bool) -> np.ndarray:
    img = Image.fromarray(rgb, mode="RGB")
    d = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    q = img.quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=d)
    return np.asarray(q.convert("RGB"))


def _edge_seeds(alpha: np.ndarray) -> list[tuple[int, int]]:
    """Still-opaque pixels on the frame's border, where a backdrop must reach."""
    h, w = alpha.shape[:2]
    seeds = []
    for x in range(w):
        for y in (0, h - 1):
            if alpha[y, x]:
                seeds.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if alpha[y, x]:
                seeds.append((y, x))
    return seeds


def _flood(rgb: np.ndarray, alpha: np.ndarray, seeds: list[tuple[int, int]],
           tol: int) -> np.ndarray:
    """Clear everything reachable from `seeds` within `tol` of the seed's colour."""
    h, w = alpha.shape[:2]
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
    return trial


def background_to_alpha(rgb: np.ndarray, tol: int, passes: int = 3,
                        keep_min: float = 0.04,
                        key: tuple[int, int, int] | None = None) -> np.ndarray:

    h, w = rgb.shape[:2]
    alpha = np.full((h, w), 255, dtype=np.uint8)

    if key is not None:
        want = np.asarray(key, dtype=np.int16)
        near = (np.abs(rgb[..., :3].astype(np.int16) - want).max(axis=2) <= tol * 3)
        if near.mean() < 1.0 - keep_min:
            alpha[near] = 0

    for _ in range(max(1, passes)):
        seeds = _edge_seeds(alpha)
        if not seeds:
            break

        trial = _flood(rgb, alpha, seeds, tol)
        if (trial > 0).mean() < keep_min:
            break
        if (trial > 0).sum() == (alpha > 0).sum():
            break
        alpha = trial

    return np.dstack([rgb, alpha])


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
