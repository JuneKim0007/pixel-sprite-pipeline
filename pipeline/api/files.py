"""Browsing, uploading and downloading, inside the permitted roots.

Every path a browser supplies is resolved against `allowed_roots` before it is
opened. The configured input, output and download directories may sit outside
the project, so the list is explicit rather than assuming everything is under
ROOT.

Streaming a file and reading a multipart upload stay in server.py: those need
the socket, and a handler that returns a dict cannot express them. They are
declared `raw` in the route table so the surface still lists them.
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

from .. import files as files_mod
from ..shared.errors import Invalid
from .context import ROOT, allowed_roots, download_dir, human_size, input_dir
from .routing import BaseRouter, get, post
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

    @get("/file", "stream one file", raw=True)
    def file(self, req):
        raise NotImplementedError("served by the HTTP layer")

    @post("/upload", "receive files", raw=True)
    def upload(self, req):
        raise NotImplementedError("served by the HTTP layer")
