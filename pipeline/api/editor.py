"""The definitive editor: an ordered layer stack over one image."""

from __future__ import annotations

import base64
import threading
import io
from pathlib import Path

import numpy as np
from PIL import Image

from .. import definitive
from ..definitive import pngstream
from ..shared import limits
from ..shared import files as files_mod
from ..shared.errors import TooLarge
from .context import ROOT, allowed_roots
from .routing import BaseRouter, get, post

DEFERRED = {"scale"}


def _editor_source(path_str: str) -> Path:
    return files_mod.safe_path(path_str, allowed_roots())


def _palette_path(name: str) -> Path:
    from ..looks.palettes import registry

    return Path(registry(ROOT).get(name).path)


_GATE = threading.Semaphore(limits.get("concurrent"))


def _fit_for_preview(image: Image.Image) -> tuple[Image.Image, float]:

    edge = limits.get("preview_edge")
    longest = max(image.size)
    if longest <= edge:
        return image, 1.0
    scale = edge / longest
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image.resize(size, Image.NEAREST), scale


def _open_bounded(src: Path) -> Image.Image:
    """Without this the first thing every request did was materialise the whole source at 3 bytes a pixel, whatever it was about to shrink it to."""
    try:
        probe = Image.open(src)
    except Image.DecompressionBombError as e:
        raise TooLarge(
            f"{src.name} is too large for the decoder to open.",
            field="source",
            hint="Written at a high zoom, most likely. The file is fine - it "
                 "is the source it was made from that the editor wants.") from e

    pixels = probe.width * probe.height
    ceiling = limits.get("output_pixels")
    if pixels > ceiling:
        raise TooLarge(
            f"{src.name} is {pixels / 1e6:.1f} MP, over the "
            f"{ceiling / 1e6:.1f} MP this editor will decode.",
            field="source",
            hint="Edit the sprite at its own size and let Zoom magnify it.")
    return probe.convert("RGB")


def _run(body: dict, *, full: bool) -> tuple[np.ndarray, dict, Path, dict]:
    src = _editor_source(body.get("source", ""))
    original = _open_bounded(src)
    stack = body.get("stack") or definitive.default_stack()
    image_in, scale = (original, 1.0) if full else _fit_for_preview(original)

    with _GATE:
        out, facts = definitive.apply_stack(
            np.asarray(image_in), stack, palettes=_palette_path,
            source=f'{src}@{image_in.width}x{image_in.height}',
            defer=DEFERRED)

    facts["preview"] = {
        "scale": round(scale, 4),
        "computed_at": list(image_in.size),
        "source_at": list(original.size),
        "exact": scale == 1.0,
    }
    return out, facts, src, facts.get("deferred") or {"scale": 1.0}


def edit_preview(body: dict) -> dict:

    out, facts, src, deferred = _run(body, full=bool(body.get("full")))

    image = Image.fromarray(out, mode="RGBA" if out.shape[2] == 4 else "RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return {
        "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
        "source": str(src),
        "zoom": deferred.get("scale", 1.0),
        "facts": facts,
    }


def edit_apply(body: dict) -> dict:

    out, facts, src, deferred = _run(body, full=True)

    dest = body.get("dest") or str(src.with_name(f"{src.stem}_px.png"))
    target = files_mod.safe_path(dest, allowed_roots())
    zoom = max(1, int(round(float(deferred.get("scale", 1.0)))))
    width, height = pngstream.write_scaled(target, out, zoom)

    return {"written": str(target), "width": width, "height": height,
            "zoom": zoom, "facts": facts}


def editor_layers() -> dict:
    """The layer catalogue and a starting stack, for building the form."""
    from pipeline.looks.palettes import discover

    cat = definitive.catalogue()
    names = sorted(discover(ROOT))
    for spec in cat:
        for f in spec["fields"]:
            if f["key"] == "file" and spec["key"] == "palette":
                f["options"] = [[n, n] for n in names]
    return {"layers": cat, "default_stack": definitive.default_stack(),
            "limits": {"preview_edge": limits.get("preview_edge"),
                       "output_pixels": limits.get("output_pixels"),
                       "deferred": sorted(DEFERRED)}}


class Editor(BaseRouter):
    prefix = "/api/edit"

    @post("/preview", "run a layer stack and return the image inline")
    def preview(self, req):
        return edit_preview(req.body)

    @post("/apply", "run a layer stack and write the result")
    def apply(self, req):
        return edit_apply(req.body)


class Layers(BaseRouter):
    prefix = "/api/editor"

    @get("/layers", "the layer catalogue and a starting stack")
    def catalogue(self, req):
        return editor_layers()
