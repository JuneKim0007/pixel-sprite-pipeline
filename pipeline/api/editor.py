"""The definitive editor: an ordered layer stack over one image.
"""

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

# Layers the editor computes by describing rather than by running. Scale is the
# only one and is marked `deferrable` at its definition; naming it here is the
# request, not the permission, so a layer that is not deferrable stays run
# whatever this says.
DEFERRED = {"scale"}


def _editor_source(path_str: str) -> Path:
    return files_mod.safe_path(path_str, allowed_roots())



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
    """Decode a source, refusing one too big to decode.

    PIL reads the header lazily, so the dimensions are known before any pixels
    are, and refusing here costs nothing. Without this the first thing every
    request did was materialise the whole source at 3 bytes a pixel, whatever
    it was about to shrink it to.

    Two ceilings, because PIL has its own and it is the higher of the two.
    Above roughly 179 MP `Image.open` raises DecompressionBombError from inside
    the constructor, before any check here could run - so that has to be caught
    rather than pre-empted. It is reachable in normal use: the write path can
    now stream a 268 MP file that PIL will not reopen, and someone loading
    their own output back into the editor deserves a sentence about it and not
    a 500.
    """
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
    """The shared half of preview and write: decode, run the stack, describe.

    Magnification is deferred in both. For a preview the viewer reproduces it
    exactly; for a write the encoder applies it a scanline at a time. Neither
    needs the magnified array, and the magnified array is the whole problem -
    so it is never built rather than being built carefully.
    """
    src = _editor_source(body.get("source", ""))
    original = _open_bounded(src)
    stack = body.get("stack") or definitive.default_stack()
    image_in, scale = (original, 1.0) if full else _fit_for_preview(original)

    with _GATE:
        # The source path plus the preview scale identifies the input, so a
        # full-resolution run never resumes from a preview-sized snapshot.
        out, facts = definitive.apply_stack(
            np.asarray(image_in), stack, root=ROOT,
            source=f'{src}@{image_in.width}x{image_in.height}',
            defer=DEFERRED)

    facts["preview"] = {
        "scale": round(scale, 4),
        "computed_at": list(image_in.size),
        "source_at": list(original.size),
        # A block size measured on a shrunk image is a block size in shrunk
        # pixels. Saying so is the difference between a number and a wrong one.
        "exact": scale == 1.0,
    }
    return out, facts, src, facts.get("deferred") or {"scale": 1.0}


def edit_preview(body: dict) -> dict:

    out, facts, src, deferred = _run(body, full=bool(body.get("full")))

    # Sent at the size it was computed at. The browser magnifies it on the way
    # to the screen - every result surface carries `image-rendering: pixelated`,
    # which IS nearest-neighbour - so shipping the magnified pixels meant
    # encoding, base64-ing and transferring an image the viewer then scaled
    # back down to fit the panel. `facts.deferred` carries the zoom it owes.
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

    # Writing a file always runs at full resolution, whatever the preview did.
    out, facts, src, deferred = _run(body, full=True)

    dest = body.get("dest") or str(src.with_name(f"{src.stem}_px.png"))
    target = files_mod.safe_path(dest, allowed_roots())
    zoom = max(1, int(round(float(deferred.get("scale", 1.0)))))
    width, height = pngstream.write_scaled(target, out, zoom)

    # The file, not its bytes. A data URI cost six live copies of the result on
    # the way out - array, PIL image, encoder buffer, base64 at 4/3, its str,
    # then the JSON - to hand back something the browser only wanted a URL for.
    return {"written": str(target), "width": width, "height": height,
            "zoom": zoom, "facts": facts}


def editor_layers() -> dict:
    """The layer catalogue and a starting stack, for building the form."""
    from pipeline.looks.palettes import discover

    cat = definitive.catalogue()
    names = sorted(discover(ROOT))
    # The palette picker's options are the files on disk, so a layer never
    # hardcodes a list that a new .hex would fall outside of.
    for spec in cat:
        for f in spec["fields"]:
            if f["key"] == "file" and spec["key"] == "palette":
                f["options"] = [[n, n] for n in names]
    # The browser needs the same preview budget the server uses, or the two
    # paths disagree about how much work a preview is.
    return {"layers": cat, "default_stack": definitive.default_stack(),
            "limits": {"preview_edge": limits.get("preview_edge"),
                       "output_pixels": limits.get("output_pixels"),
                       # Which layers the browser is expected to reproduce
                       # itself. Sent rather than hardcoded, so a layer that
                       # stops being deferrable stops being deferred here too.
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
