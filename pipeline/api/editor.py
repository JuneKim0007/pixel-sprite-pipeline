"""The definitive editor: an ordered layer stack over one image.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image

from .. import definitive
from .. import files as files_mod
from .context import ROOT, allowed_roots
from .routing import BaseRouter, get, post


def _editor_source(path_str: str) -> Path:
    return files_mod.safe_path(path_str, allowed_roots())


def edit_preview(body: dict) -> dict:
    """Run a layer stack over one image and return the result inline.

    The stack is whatever the editor sends. A missing one falls back to the
    default arrangement rather than erroring, so a client that has not yet
    built a stack still gets a picture.
    """



    src = _editor_source(body.get("source", ""))
    original = Image.open(src).convert("RGB")
    stack = body.get("stack") or definitive.default_stack()

    out, facts = definitive.apply_stack(np.asarray(original), stack, root=ROOT)

    image = Image.fromarray(out, mode="RGBA" if out.shape[2] == 4 else "RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return {
        "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
        "source": str(src),
        "facts": facts,
    }


def edit_apply(body: dict) -> dict:

    result = edit_preview(body)
    src = Path(result["source"])
    dest = body.get("dest") or str(src.with_name(f"{src.stem}_px.png"))
    target = files_mod.safe_path(dest, allowed_roots())
    payload = result["image"].split(",", 1)[1]
    target.write_bytes(base64.b64decode(payload))
    return {"written": str(target), "facts": result["facts"]}


def editor_layers() -> dict:
    """The layer catalogue and a starting stack, for building the form."""
    from pipeline.palettes import discover

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
