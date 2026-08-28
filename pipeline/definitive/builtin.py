
from __future__ import annotations

import numpy as np

from . import pixelize as px
from .layers import Field, layer
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
    measured = px.estimate_block_size(img)
    factor = int(cfg.get("factor") or 0) or max(1, int(round(measured)))
    factor = max(1, min(factor, min(img.shape[:2]) // 2 or 1))

    if factor <= 1:
        return {"measured_block": measured, "factor": factor, "phase": [0, 0]}
    if cfg.get("phase", "auto") == "auto":
        ox, oy = px.find_phase(img, factor)
    else:
        ox, oy = int(cfg.get("phase_x", 0)), int(cfg.get("phase_y", 0))
    return {"measured_block": measured, "factor": factor, "phase": [ox, oy]}


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
    reports=frozenset({"measured_block", "factor", "phase"}),
)
def _grid(inputs, cfg, prep):
    img = inputs["image"]
    said = {"measured_block": prep["measured_block"], "factor": prep["factor"],
            "phase": prep["phase"]}
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
    return {"palette": px.generate_palette(img[..., :3], int(cfg.get("colours", 24)),
                                           method=cfg.get("match", "weighted"))}


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
    if cfg.get("fit"):
        alpha = img[..., 3] if img.shape[2] == 4 else None
        return {"image": px.fit_to_palette(
            img[..., :3], palette, method=method, alpha=alpha,
            strength=float(cfg.get("fit_strength", 1.0))), **said}
    return {"image": px.apply_fixed_palette(img[..., :3], palette,
                                            method=method), **said}


@layer(
    "background", label="Background", order=40, needs=frozenset({"reduced"}),
    reports=frozenset({"kept"}),
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
def _background(inputs, cfg, prep):
    img = inputs["image"]
    if not cfg.get("enabled", True):
        return {"image": img}
    raw = str(cfg.get("colour") or "").lstrip("#")
    key = None
    if len(raw) == 6:
        try:
            key = tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            raise Invalid(f"'{cfg.get('colour')}' is not a hex colour",
                          field="colour") from None
    out = px.background_to_alpha(img[..., :3], int(cfg.get("tolerance", 14)), key=key)
    return {"image": out, "kept": float((out[..., 3] > 0).mean())}


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
