
from __future__ import annotations


import numpy as np
from PIL import Image

from . import pixelize as px
from .layers import Field, layer
from ..shared.colour import BACKDROP_PRESETS, parse_colour
from ..shared.errors import Invalid

REDUCERS = [
    ("median", "Median, robust to a stray bright pixel"),
    ("salient", "Salient, keeps the extreme where a block has contrast"),
    ("mode", "Mode, the most common exact colour"),
    ("mean", "Mean, smooth and most likely to invent a colour"),
]
MATCHERS = [
    ("weighted", "Weighted, luminance-weighted RGB"),
    ("luma", "Luma, brightness first"),
    ("lab", "Lab, perceptually uniform"),
    ("rgb", "RGB, plain euclidean"),
]


@layer(
    "curves", label="Curves", order=10,
    summary="Tone, before anything is quantised",
    fields=[
        Field("gamma", "Gamma", "float", min=0.4, max=2.5, step=0.05, default=1.0,
              help="Redistributes values inside the range instead of shifting "
                   "the whole range. This is what 'the shadows are too dark "
                   "but the highlights are fine' actually needs."),
        Field("contrast", "Contrast", "float", min=0.4, max=2.5, step=0.05, default=1.05,
              help="Where this layer sits decides what it does. Before the "
                   "palette, lifting contrast pushes midtones out to the ends "
                   "of the ramp and the sprite takes up its light and dark "
                   "entries. After it, the same adjustment just moves colours "
                   "off the palette again."),
        Field("brightness", "Brightness", "float", min=-0.4, max=0.4, step=0.02, default=0.0,
              help="Shifts every value equally, which flattens the ramp. "
                   "Gamma is usually the better tool."),
        Field("saturation", "Saturation", "float", min=0.0, max=2.5, step=0.05, default=1.0,
              help="Colour intensity. Dropping it toward 0 is how a limited "
                   "palette gets a muted variant of the same art."),
    ],
)
def _curves(inputs, cfg, prep):
    img = inputs["image"]
    if all(float(cfg.get(k, d)) == d for k, d in
           (("gamma", 1.0), ("contrast", 1.0), ("brightness", 0.0), ("saturation", 1.0))):
        return {"image": img}
    return {"image": px.curves(
        img,
        gamma=float(cfg.get("gamma", 1.0)),
        contrast=float(cfg.get("contrast", 1.0)),
        brightness=float(cfg.get("brightness", 0.0)),
        saturation=float(cfg.get("saturation", 1.0)))}


def _grid_prepare(inputs, cfg) -> dict:
    img = inputs["image"]
    mode = cfg.get("measure", "auto")
    measured = px.detect_block(img, mode)
    exact, periodic = px.block_candidates(img) if mode == "auto" else (0, 0)
    # Only where harmonics cannot explain it: the choice was a judgement, not a reading.
    unsure = (exact > 1 and periodic > 1 and exact != periodic
              and periodic % exact and exact % periodic)
    factor = int(cfg.get("factor") or 0) or max(1, int(round(measured)))
    factor = max(1, min(factor, min(img.shape[:2]) // 2 or 1))

    said = {"disputed": f"exact {exact}, periodic {periodic}" if unsure else ""}
    if factor <= 1:
        return {"measured_block": measured, "factor": factor, "phase": [0, 0],
                **said}
    if cfg.get("phase", "auto") == "auto":
        ox, oy = px.find_phase(img, factor)
    else:
        ox, oy = int(cfg.get("phase_x", 0)), int(cfg.get("phase_y", 0))
    return {"measured_block": measured, "factor": factor, "phase": [ox, oy],
            **said}


@layer(
    "grid", label="Grid", order=20, gives=frozenset({"reduced"}),
    summary="Screen pixels collapse into logical ones",
    fields=[
        Field("factor", "Block size", "int", min=0, max=64, step=1, default=0,
              help="How many screen pixels become one logical pixel. Zero "
                   "measures it: the largest factor that reduces the image "
                   "without loss. Override only when the measurement is "
                   "visibly wrong, because it is a property of the picture "
                   "rather than a preference."),
        Field("measure", "How to measure", "select", default="auto",
              options=[("auto", "Auto, exact then periodic"),
                       ("exact", "Exact, blocks that average losslessly"),
                       ("periodic", "Periodic, how regular the edges are"),
                       ("off", "Do not measure, treat as 1:1")],
              when={"factor": 0},
              help="Exact asks whether k x k blocks average without loss, "
                   "which is precise on a clean source and fails on a "
                   "compressed one: a real 5px lattice scored 3.8% against a "
                   "2% threshold and came back as 1. Periodic asks how regular "
                   "the edges are, and noise is not regular - it recovered 18 "
                   "of 35 grids exact had missed, and agreed with exact "
                   "wherever exact had an answer. Auto takes exact when it "
                   "finds one and falls back to periodic."),
        Field("phase", "Grid origin", "select", default="auto",
              options=[("auto", "Auto, minimum variance inside each block"),
                       ("manual", "Manual")],
              help="Where the lattice starts. The generated grid rarely begins "
                   "at 0,0 and sampling on the wrong phase straddles block "
                   "boundaries, smearing two logical pixels into one. This is "
                   "the single biggest cause of muddy output."),
        Field("phase_x", "Origin x", "int", min=0, max=63, step=1, default=0,
              when={"phase": "manual"}, help="Horizontal offset of the lattice."),
        Field("phase_y", "Origin y", "int", min=0, max=63, step=1, default=0,
              when={"phase": "manual"}, help="Vertical offset of the lattice."),
        Field("reduce", "Block reduce", "select", default="median", options=REDUCERS,
              help="How the pixels inside one block collapse to a single "
                   "colour. Median measured 100% structural accuracy against "
                   "ground truth where mode managed 70%, because on "
                   "anti-aliased input almost every pixel is unique and the "
                   "most frequent one is arbitrary."),
    ],
    prepare=_grid_prepare,
    reports=frozenset({"measured_block", "factor", "phase", "disputed"}),
)
def _grid(inputs, cfg, prep):
    img = inputs["image"]
    said = {"measured_block": prep["measured_block"], "factor": prep["factor"],
            "phase": prep["phase"], "disputed": prep.get("disputed", "")}
    if prep["factor"] <= 1:
        return {"image": img, **said}
    ox, oy = prep["phase"]
    return {"image": px.reduce_blocks(img, prep["factor"], ox, oy,
                                      cfg.get("reduce", "median")), **said}


def _palette_prepare(inputs, cfg) -> dict:
    img = inputs["image"]
    source = cfg.get("source", "generate")
    if source == "none":
        return {"palette": None}
    if source == "file":
        name = cfg.get("file") or ""
        if not name:
            return {"palette": None}
        return {"palette": None, "file": name}
    alpha = img[..., 3] if img.shape[2] == 4 else None
    if cfg.get("build") == "ramps":
        return {"palette": px.ramp_palette(
            img[..., :3], int(cfg["colours"]), alpha=alpha,
            method=cfg.get("match", "rgb"), ramps=int(cfg.get("ramps", 0)))}
    return {"palette": px.anchored_palette(
        img[..., :3], int(cfg["colours"]), alpha=alpha,
        method=cfg.get("match", "rgb"),
        keep_black=bool(cfg.get("preserve_black", False)),
        keep_white=bool(cfg.get("preserve_white", False)))}


@layer(
    "palette", label="Palette", order=30, needs=frozenset({"reduced"}),
    reports=frozenset({"palette_size"}),
    summary="A bounded set of colours, imposed exactly",
    prepare=_palette_prepare,
    fields=[
        Field("source", "Colours from", "select", default="generate",
              options=[("generate", "Generate from this image"),
                       ("file", "A committed palette"),
                       ("none", "Leave the colours alone")],
              help="A committed palette is what keeps separate runs of one "
                   "character on-model: snapping is deterministic, so colour "
                   "stops being probabilistic and becomes exact."),
        Field("file", "Palette", "select", default="", when={"source": "file"},
              help="A file under palettes/."),
        Field("colours", "How many", "int", min=2, max=256, step=1, default=16,
              when={"source": "generate"},
              help="Clustered in the matching metric's own space rather than "
                   "by median cut. Median cut subdivides the RGB cube and "
                   "will spend five of eight entries inside one midtone: "
                   "measured luminances 52,144,145,145,145,145,148,227, a "
                   "palette with almost no value range for a medium that "
                   "reads by value."),
        Field("build", "Built as", "select", default="clusters",
              options=[("clusters", "Clusters, one pass over every pixel"),
                       ("ramps", "Ramps, colour families then shades")],
              when={"source": "generate"},
              help="Clustering minimises variance weighted by pixel COUNT, so "
                   "it spends entries where pixels are dense rather than where "
                   "they differ: several near-identical tones for the largest "
                   "garment and none left for an accent. Ramps group by chroma "
                   "first and take shades inside each group, which is how a "
                   "pixel artist builds a palette and gives every family its "
                   "own value range."),
        Field("ramps", "Colour families", "int", min=0, max=16, step=1, default=0,
              when={"build": "ramps"},
              help="0 takes the square root of the colour count - 3 families "
                   "for 10 colours. Raise it for a character carrying many "
                   "distinct materials, lower it for a limited one."),
        Field("preserve_black", "Keep pure black", "bool", default=False,
              when={"source": "generate"},
              help="Pins 0,0,0 as an entry when the art actually uses it, and "
                   "withholds those pixels from the clustering. Without it "
                   "k-means puts a centre NEAR black - 8,6,10 - and every dark "
                   "pixel lands on it, so black spreads: measured 12.4% of one "
                   "sprite becoming 18.5%. Pinning the corner more than halved "
                   "the error against the source."),
        Field("preserve_white", "Keep pure white", "bool", default=False,
              when={"source": "generate"},
              help="The same for 255,255,255. Costs one of the entries, and "
                   "only claims it when at least 0.5% of the art is already "
                   "within 16 of the corner - so a stray compression pixel "
                   "does not buy a slot."),
        Field("match", "Matching", "select", default="rgb", options=MATCHERS,
              help="How 'nearest colour' is decided, and what the clustering "
                   "measures distance in. Measured over 35 sprites at 10 "
                   "colours: rgb separated its entries best (nearest pair 55) "
                   "at the best fidelity (7.76). weighted came second on both "
                   "(42, 7.77); luma, which this help used to recommend, was "
                   "worst at separating (40) and worse on fidelity (8.34)."),
        Field("fit", "Fit to the palette's range", "bool", default=False,
              help="Stretches the picture's value range onto the palette's "
                   "before snapping. Nearest matching is absolute and cannot "
                   "widen anything: a subject spanning luminance 86 to 170 "
                   "snapped onto a palette spanning 0 to 255 comes back "
                   "spanning 86 to 170, using 11 of 32 entries with the sprite "
                   "reading flat. With this on the same case used 26 and "
                   "spanned the full range. It changes the colours in exchange "
                   "for using the palette, which is a trade rather than a fix, "
                   "so it is off by default."),
        Field("fit_strength", "Fit amount", "float", min=0.0, max=1.0, step=0.05,
              default=1.0, when={"fit": True},
              help="Below 1 interpolates back toward the original, for when a "
                   "full stretch is more than the picture wants."),
        Field("dither", "Dither", "bool", default=False,
              help="Trades flat blocks for apparent depth. Off suits a chunky "
                   "idiom; on when a small palette has to carry a gradient."),
    ],
)
def _palette(inputs, cfg, prep):
    img = inputs["image"]
    palette = prep.get("palette")

    if prep.get("file"):
        resolve = inputs.get("palettes")
        if resolve is None:
            raise Invalid("this stack reads a committed palette, and nothing "
                          "was supplied to resolve one by name",
                          field="palette.file",
                          hint="pass palettes= to apply_stack")
        palette = px.load_palette(resolve(prep["file"]))

    if not palette:
        return {"image": img}

    said = {"palette_size": len(palette)}
    if cfg.get("dither"):
        img = px.quantize_median_cut(img[..., :3], len(palette), True)

    method = cfg.get("match", "weighted")
    alpha = img[..., 3:] if img.shape[2] == 4 else None
    if cfg.get("fit"):
        fitted = px.fit_to_palette(
            img[..., :3], palette, method=method,
            alpha=None if alpha is None else alpha[..., 0],
            strength=float(cfg.get("fit_strength", 1.0)))
    else:
        fitted = px.apply_fixed_palette(img[..., :3], palette, method=method)
    if alpha is not None:
        fitted = np.dstack([fitted, alpha])
    return {"image": fitted, **said}


@layer(
    "background", label="Background", order=40, needs=frozenset({"reduced"}),
    reports=frozenset({"kept"}),
    summary="The backdrop becomes transparent",
    fields=[
        Field("enabled", "Key out the backdrop", "bool", default=True,
              help="Off when the background is part of the art."),
        Field("method", "How", "select", default="flood",
              options=[("flood", "Flood from the frame's edge"),
                       ("ring", "Census the border's colours first")],
              when={"enabled": True},
              help="Flood compares every pixel to its SEED, and on a JPEG the "
                   "seeds are the most compressed pixels in the frame - it "
                   "clears a two-pixel border and stops. Ring counts what "
                   "colours the border is made of and floods their union, "
                   "which crosses a checkerboard and reaches a panel inset "
                   "behind a margin. Ring is the one the training tools use."),
        Field("tolerance", "Tolerance", "int", min=0, max=64, step=1, default=14,
              when={"enabled": True},
              help="Colour distance from the backdrop that still counts as "
                   "background. Raise it when a two-tone backdrop survives, "
                   "lower it when the sprite starts losing its own dark "
                   "edges."),
        Field("colour", "Backdrop colour", "colour", default="",
              options=[(h, l) for h, l in BACKDROP_PRESETS],
              when={"enabled": True},
              help="RGB as '12, 34, 56' or hex as '#0a1b2c', if you know what "
                   "the backdrop is. Then the keyer removes that exact hue "
                   "instead of flooding from a corner and guessing, which "
                   "also reaches a gap enclosed by the character that a flood "
                   "cannot. Leave it blank to flood."),
    ],
)
def _background(inputs, cfg, prep):
    img = inputs["image"]
    if not cfg.get("enabled", True):
        return {"image": img}
    tolerance = int(cfg.get("tolerance", 14))
    if cfg.get("method") == "ring":
        from ..shared import keying

        alpha = keying.key_backdrop(img[..., :3], max(tolerance, 1))
        out = np.dstack([img[..., :3], alpha])
    else:
        key = parse_colour(cfg.get("colour"))
        out = px.background_to_alpha(img[..., :3], tolerance, key=key)
    return {"image": out, "kept": float((out[..., 3] > 0).mean())}


@layer(
    "canvas", label="Canvas", order=50,
    reports=frozenset({"canvas_size"}),
    summary="The subject, seated on a fixed sprite canvas",
    fields=[
        Field("width", "Width", "int", min=0, max=1024, step=8, default=0,
              help="0 leaves the image its own size. Set it with height to "
                   "seat the subject on a fixed sprite canvas, which is what "
                   "a sheet needs and what Scale cannot do - Scale multiplies, "
                   "it does not frame."),
        Field("height", "Height", "int", min=0, max=1024, step=8, default=0,
              help="Taller than the width suits a standing figure. A square "
                   "canvas spends its corners on backdrop."),
        Field("fill", "Fill", "float", min=0.1, max=1.0, step=0.02, default=0.92,
              help="Fraction of the tightest axis the subject spans. Below 1 "
                   "leaves the margin a sprite sheet needs to avoid clipping "
                   "when a neighbouring frame is wider."),
        Field("binary_alpha", "Hard edges", "bool", default=True,
              help="A sprite edge is in or out. A half-transparent pixel is a "
                   "colour the palette never chose, and it survives every "
                   "later layer as a fringe."),
    ],
)
def _canvas(inputs, cfg, prep):
    from ..shared.canvas import seat

    from ..shared import canvas as canvas_mod

    img = inputs["image"]
    fill = None
    width, height = int(cfg.get("width", 0)), int(cfg.get("height", 0))
    if width <= 0 or height <= 0:
        # Grid runs first, so the art is already at final size; what holds it is all that is left.
        found = canvas_mod.fitting(img.shape[1], img.shape[0])
        if found is None:
            return {"image": img, "canvas_size": ""}
        # Seating is a pad, never a rescale: resampling adds colours the palette did not choose.
        width, height = found
        fill = 1.0
    art = Image.fromarray(np.ascontiguousarray(img))
    if fill is None:
        fill = float(cfg.get("fill", 0.92))
    out = seat(art, (width, height), fill, shrink_only=True)
    a = np.asarray(out).copy()
    if a.shape[2] == 4 and cfg.get("binary_alpha", True):
        a[..., 3] = np.where(a[..., 3] >= 128, 255, 0)
    return {"image": a, "canvas_size": f"{width}x{height}"}


@layer(
    "scale", label="Scale", order=90,
    summary="Nearest-neighbour, so nothing is invented",
    fields=[
        Field("upscale", "Zoom", "int", min=1, max=16, step=1, default=4,
              help="Nearest neighbour, so it magnifies without inventing "
                   "anything. Applied to the written file too, which is what "
                   "makes a 128px sprite viewable outside a pixel-art editor."),
    ],
    # Zoom squared: the only layer that grows. At the maximum that is 256x the pixels, 16 GB.
    magnify=lambda cfg: max(1, int(cfg.get("upscale", 1))) ** 2,
    deferrable=True,
    reports=frozenset({"colours"}),
)
def _scale(inputs, cfg, prep):
    img = inputs["image"]
    n = max(1, int(cfg.get("upscale", 1)))
    if n == 1:
        return {"image": img}
    from .cache import count_colours

    return {"image": np.repeat(np.repeat(img, n, axis=0), n, axis=1),
            "colours": count_colours(img)}
