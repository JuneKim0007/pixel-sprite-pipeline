"""The five layers the editor ships with.

Each one wraps machinery that already existed in pixelize.py; nothing here
reimplements an algorithm. What is new is that the order is data and every
field carries its own explanation, so the form and the (?) beside each control
are generated rather than written by hand next to each control and forgotten
next to the next one.

`apply` takes and returns a numpy image plus a shared `facts` dict. Facts are
how a layer tells the UI what it measured - the block size it found, the phase
it settled on - which is the difference between a slider you guess with and one
that shows its answer.
"""

from __future__ import annotations

import numpy as np

from . import pixelize as px
from .layers import Field, layer

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


# --------------------------------------------------------------------- curves


@layer(
    "curves", label="Curves", order=10,
    summary="Tone, before anything is quantised",
    fields=[
        Field("gamma", "Gamma", "float", min=0.4, max=2.5, step=0.05, default=1.0,
              help="Redistributes values inside the range instead of shifting "
                   "the whole range. This is what 'the shadows are too dark "
                   "but the highlights are fine' actually needs."),
        Field("contrast", "Contrast", "float", min=0.4, max=2.5, step=0.05, default=1.0,
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
def _curves(img, cfg, facts):
    if all(float(cfg.get(k, d)) == d for k, d in
           (("gamma", 1.0), ("contrast", 1.0), ("brightness", 0.0), ("saturation", 1.0))):
        return img
    return px.curves(img,
                     gamma=float(cfg.get("gamma", 1.0)),
                     contrast=float(cfg.get("contrast", 1.0)),
                     brightness=float(cfg.get("brightness", 0.0)),
                     saturation=float(cfg.get("saturation", 1.0)))


# ----------------------------------------------------------------------- grid


@layer(
    "grid", label="Grid", order=20,
    summary="Screen pixels collapse into logical ones",
    fields=[
        Field("factor", "Block size", "int", min=0, max=64, step=1, default=0,
              help="How many screen pixels become one logical pixel. Zero "
                   "measures it: the largest factor that reduces the image "
                   "without loss. Override only when the measurement is "
                   "visibly wrong, because it is a property of the picture "
                   "rather than a preference."),
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
)
def _grid(img, cfg, facts):
    from .cache import measured_block

    measured = measured_block(img)
    facts["measured_block"] = measured
    factor = int(cfg.get("factor") or 0) or max(1, int(round(measured)))
    factor = max(1, min(factor, min(img.shape[:2]) // 2 or 1))
    facts["factor"] = factor
    if factor <= 1:
        facts["phase"] = [0, 0]
        return img

    if cfg.get("phase", "auto") == "auto":
        from .cache import phase_for

        ox, oy = phase_for(img, factor)
    else:
        ox, oy = int(cfg.get("phase_x", 0)), int(cfg.get("phase_y", 0))
    facts["phase"] = [ox, oy]
    return px.reduce_blocks(img, factor, ox, oy, cfg.get("reduce", "median"))


# -------------------------------------------------------------------- palette


@layer(
    "palette", label="Palette", order=30,
    summary="A bounded set of colours, imposed exactly",
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
        Field("colours", "How many", "int", min=2, max=256, step=1, default=24,
              when={"source": "generate"},
              help="Clustered in the matching metric's own space rather than "
                   "by median cut. Median cut subdivides the RGB cube and "
                   "will spend five of eight entries inside one midtone: "
                   "measured luminances 52,144,145,145,145,145,148,227, a "
                   "palette with almost no value range for a medium that "
                   "reads by value."),
        Field("match", "Matching", "select", default="weighted", options=MATCHERS,
              help="How 'nearest colour' is decided. Luma matches brightness "
                   "first and is the one built for sprites, because a sprite "
                   "reads by its value structure and an entry of the wrong "
                   "lightness collapses the form even when the hue is right."),
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
def _palette(img, cfg, facts):
    source = cfg.get("source", "generate")
    if source == "none":
        return img

    palette = None
    if source == "file":
        name = cfg.get("file") or ""
        if not name:
            return img
        from ..looks.palettes import discover
        from pathlib import Path

        found = discover(facts["root"]).get(name)
        if not found:
            raise FileNotFoundError(f"no palette '{name}'")
        palette = px.load_palette(Path(found.path))
    else:
        from .cache import generated_palette

        # k-means over every pixel, and the answer depends only on the image
        # arriving here plus these two numbers - so editing anything else in
        # the stack must not recompute it.
        palette = generated_palette(img, int(cfg.get("colours", 24)),
                                    cfg.get("match", "weighted"))

    facts["palette_size"] = len(palette)
    if cfg.get("dither"):
        img = px.quantize_median_cut(img[..., :3], len(palette), True)

    method = cfg.get("match", "weighted")
    if cfg.get("fit"):
        # The backdrop is excluded from the measurement, or a flat key colour
        # would define one end of the range the stretch is computed against.
        alpha = img[..., 3] if img.shape[2] == 4 else None
        return px.fit_to_palette(img[..., :3], palette, method=method, alpha=alpha,
                                 strength=float(cfg.get("fit_strength", 1.0)))
    return px.apply_fixed_palette(img[..., :3], palette, method=method)


# ----------------------------------------------------------------- background


@layer(
    "background", label="Background", order=40,
    summary="The backdrop becomes transparent",
    fields=[
        Field("enabled", "Key out the backdrop", "bool", default=True,
              help="Off when the background is part of the art."),
        Field("tolerance", "Tolerance", "int", min=0, max=64, step=1, default=14,
              when={"enabled": True},
              help="Colour distance from the backdrop that still counts as "
                   "background. Raise it when a two-tone backdrop survives, "
                   "lower it when the sprite starts losing its own dark "
                   "edges."),
        Field("colour", "Named colour", "text", default="",
              when={"enabled": True},
              help="Hex, if the prompt named the backdrop. Then the keyer "
                   "removes that exact hue instead of flooding from a corner "
                   "and guessing, which also reaches a gap enclosed by the "
                   "character that a flood cannot."),
    ],
)
def _background(img, cfg, facts):
    if not cfg.get("enabled", True):
        return img
    raw = str(cfg.get("colour") or "").lstrip("#")
    key = tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4)) if len(raw) == 6 else None
    out = px.background_to_alpha(img[..., :3], int(cfg.get("tolerance", 14)), key=key)
    facts["kept"] = float((out[..., 3] > 0).mean())
    return out


# --------------------------------------------------------------------- scale


@layer(
    "scale", label="Scale", order=90,
    summary="Nearest-neighbour, so nothing is invented",
    fields=[
        Field("upscale", "Zoom", "int", min=1, max=16, step=1, default=4,
              help="Nearest neighbour, so it magnifies without inventing "
                   "anything. Applied to the written file too, which is what "
                   "makes a 128px sprite viewable outside a pixel-art editor."),
    ],
)
def _scale(img, cfg, facts):
    n = max(1, int(cfg.get("upscale", 1)))
    if n == 1:
        return img
    return np.repeat(np.repeat(img, n, axis=0), n, axis=1)
