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
from ..shared import limits
from ..shared import files as files_mod
from .context import ROOT, allowed_roots
from .routing import BaseRouter, get, post


def _editor_source(path_str: str) -> Path:
    return files_mod.safe_path(path_str, allowed_roots())


# One preview at a time. Two in flight is always waste - only the last one is
# wanted - and it was the shape of the crash: every parameter change started a
# 363 MB job and nothing stopped them overlapping.
_GATE = threading.Semaphore(limits.get("concurrent"))


def _fit_for_preview(image: Image.Image) -> tuple[Image.Image, float]:
    """Shrink to the interactive budget, and say by how much.

    Judging a block size or a palette does not need the full canvas, and the
    cost is quadratic in the edge: 1280 to 384 is an eleven-fold reduction in
    work for a preview that answers the same questions. Writing a file does not
    come through here.

    NEAREST, not LANCZOS. A smooth resample invents intermediate colours and
    would change the very thing being measured - the block size is recovered by
    asking which factor reduces without loss, and interpolation destroys that
    evidence.
    """
    edge = limits.get("preview_edge")
    longest = max(image.size)
    if longest <= edge:
        return image, 1.0
    scale = edge / longest
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image.resize(size, Image.NEAREST), scale


def edit_preview(body: dict) -> dict:
    """Run a layer stack over one image and return the result inline.

    The stack is whatever the editor sends. A missing one falls back to the
    default arrangement rather than erroring, so a client that has not yet
    built a stack still gets a picture.
    """
    src = _editor_source(body.get("source", ""))
    original = Image.open(src).convert("RGB")
    stack = body.get("stack") or definitive.default_stack()

    full = bool(body.get("full"))
    image_in, scale = (original, 1.0) if full else _fit_for_preview(original)

    with _GATE:
        # The source path plus the preview scale identifies the input, so a
        # full-resolution run never resumes from a preview-sized snapshot.
        out, facts = definitive.apply_stack(
            np.asarray(image_in), stack, root=ROOT,
            source=f'{src}@{image_in.width}x{image_in.height}')

    facts["preview"] = {
        "scale": round(scale, 4),
        "computed_at": list(image_in.size),
        "source_at": list(original.size),
        # A block size measured on a shrunk image is a block size in shrunk
        # pixels. Saying so is the difference between a number and a wrong one.
        "exact": scale == 1.0,
    }

    image = Image.fromarray(out, mode="RGBA" if out.shape[2] == 4 else "RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return {
        "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
        "source": str(src),
        "facts": facts,
    }


def edit_apply(body: dict) -> dict:

    # Writing a file always runs at full resolution, whatever the preview did.
    result = edit_preview({**body, "full": True})
    src = Path(result["source"])
    dest = body.get("dest") or str(src.with_name(f"{src.stem}_px.png"))
    target = files_mod.safe_path(dest, allowed_roots())
    payload = result["image"].split(",", 1)[1]
    target.write_bytes(base64.b64decode(payload))
    return {"written": str(target), "facts": result["facts"]}


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
    return {"layers": cat, "default_stack": definitive.default_stack()}


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
