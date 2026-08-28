#!/usr/bin/env python3


from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from ..shared.errors import Invalid, NotFound

REDUCE_MODES = ("mean", "median", "mode", "clipped", "salient")


def _blocks(arr: np.ndarray, factor: int, ox: int, oy: int) -> np.ndarray:
    """Crop to the phase (ox, oy) and reshape into (bh, bw, factor, factor, C)."""
    h, w = arr.shape[:2]
    bh = (h - oy) // factor
    bw = (w - ox) // factor
    if bh < 1 or bw < 1:
        raise Invalid(f"factor {factor} too large for image {w}x{h}", field="factor")
    cropped = arr[oy : oy + bh * factor, ox : ox + bw * factor]
    c = arr.shape[2]
    return cropped.reshape(bh, factor, bw, factor, c).swapaxes(1, 2)


def find_phase(arr: np.ndarray, factor: int) -> tuple[int, int]:

    best, best_cost = (0, 0), float("inf")
    for oy in range(factor):
        for ox in range(factor):
            blocks = _blocks(arr.astype(np.float32), factor, ox, oy)
            cost = float(blocks.var(axis=(2, 3)).sum())
            if cost < best_cost:
                best, best_cost = (ox, oy), cost
    return best


SALIENT_THRESHOLD = 34.0


_CLIP_FLOOR = 0.35


def estimate_block_size(arr, candidates: tuple[int, ...] = (1, 2, 3, 4, 6, 8, 12, 16)) -> float:
    """A sprite drawn in eight-pixel blocks loses nothing when averaged in eight-pixel blocks, so its reconstruction error at factor 8 is near zero and rises sharply at 12."""
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
        # Clipping drops the outliers, so a 90%-one-colour block returns that colour cleanly.
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
            out[..., 3] = np.median(flat[..., 3], axis=2).round().astype(np.uint8)
        return out

    if how == "salient":

        med = np.median(flat, axis=2)
        deviation = np.abs(flat - med[:, :, None, :]).sum(axis=3)
        extreme = np.take_along_axis(
            flat, deviation.argmax(axis=2)[:, :, None, None], axis=2)[:, :, 0, :]
        spread = flat.std(axis=2).mean(axis=2)
        contrasty = spread > SALIENT_THRESHOLD
        return np.where(contrasty[..., None], extreme, med).round().astype(np.uint8)

    raise NotFound("reduce mode", how, available=list(REDUCE_MODES))


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


def generate_palette(rgb: np.ndarray, colours: int, *, method: str = "weighted",
                     iterations: int = 12,
                     alpha: np.ndarray | None = None) -> list[tuple[int, int, int]]:

    pixels = rgb.reshape(-1, 3)
    if alpha is not None:
        pixels = pixels[alpha.reshape(-1) > 0]
    if len(pixels) == 0:
        raise ValueError("no opaque pixels to build a palette from")
    colours = max(1, min(int(colours), len(np.unique(pixels, axis=0))))

    feats = project(pixels, method)
    rng = np.random.default_rng(0)

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
