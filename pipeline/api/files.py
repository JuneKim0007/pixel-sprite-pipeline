from __future__ import annotations

import mimetypes
from pathlib import Path

from ..shared import files as files_mod
from ..shared.errors import Invalid
from .context import ROOT, allowed_roots, download_dir, human_size, input_dir
from .routing import BaseRouter, Raw, get, post
from .context import runs_dir
import re


def download(body: dict, dry_run: bool) -> dict:
    run_id = body.get("run_id", "")
    stage = body.get("stage")
    run = runs_dir() / run_id
    if not run.is_dir():
        raise FileNotFoundError(run_id)

    sources: list[Path] = []
    for d in sorted(run.iterdir()):
        if not d.is_dir() or not re.match(r"^\d\d_", d.name):
            continue
        if stage and d.name.split("_", 1)[1] != stage:
            continue
        sources += sorted(d.glob("*.png"))
    if not sources:
        raise FileNotFoundError("nothing to download for that selection")

    target_raw = body.get("target") or str(download_dir() / run_id)
    target = files_mod.safe_path(target_raw, allowed_roots())

    if dry_run:
        return files_mod.plan_copy(sources, target)
    return files_mod.copy_files(sources, target, bool(body.get("overwrite")))


class Files(BaseRouter):
    prefix = "/api"

    @get("/browse", "list a directory inside the permitted roots")
    def browse(self, req):
        target = req.query("path") or str(input_dir())
        safe = files_mod.safe_path(target, allowed_roots())
        return files_mod.browse(safe, images_only=req.flag("images"))

    @post("/download/plan", "what a download would fetch, without fetching")
    def plan(self, req):
        return download(req.body, dry_run=True)

    @post("/download", "fetch a model")
    def fetch(self, req):
        return download(req.body, dry_run=False)

    @get("/file", "stream one file")
    def file(self, req):
        p = files_mod.safe_path(req.required("path"), allowed_roots())
        if not p.is_file():
            raise FileNotFoundError(req.query("path"))
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        return Raw(p.read_bytes(), ctype)

    @post("/upload", "receive files")
    def upload(self, req):
        if "multipart/form-data" not in req.content_type:
            raise Invalid("expected multipart/form-data")
        target = input_dir()
        saved = []
        for _, filename, data in files_mod.parse_multipart(req.raw_body,
                                                           req.content_type):
            if not filename or not data:
                continue
            dst = files_mod.unique_name(target, files_mod.safe_filename(filename))
            dst.write_bytes(data)
            saved.append({"name": dst.name, "path": str(dst)})
        if not saved:
            raise Invalid("no files in the upload")
        return {"saved": saved, "dir": str(target)}
