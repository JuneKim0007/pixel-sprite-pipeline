"""Style sheets and palettes: the reusable half of a look."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from ..shared import files as files_mod
from ..looks import palettes as palette_lib
from ..looks import styles, stylelog
from ..shared.errors import Invalid, NotFound
from .context import CONFIGS, ROOT, allowed_roots, dump_roundtrip, load_roundtrip
from .contracts import Shape
from .routing import BaseRouter, get, post
from ..shared import errors


def _sheet(name: str) -> styles.Style:
    catalogue = styles.discover(ROOT)
    found = catalogue.get(name)
    if not found:
        raise errors.NotFound("style sheet", name, available=catalogue)
    return found


def style_detail(name: str) -> dict:
    sheet = _sheet(name)
    images = []
    for path in sheet.exemplars:
        if not path.exists():
            images.append({"path": str(path), "name": path.name, "missing": True})
            continue
        images.append({
            "path": str(path), "name": path.name,
            "bytes": path.stat().st_size, "missing": False,
        })

    pending = sheet.training_images
    archives = []
    archive_root = sheet.home / "training" / "archive"
    if archive_root.is_dir():
        for folder in sorted(archive_root.iterdir(), reverse=True):
            if folder.is_dir():
                archives.append({
                    "name": folder.name,
                    "count": sum(1 for _ in folder.iterdir()),
                })

    return {
        "name": sheet.name,
        "label": sheet.label,
        "foldered": sheet.foldered,
        "home": str(sheet.home),
        "summary": sheet.summary(ROOT),
        "context": {
            "images": images,
            "prompts": {
                "vocabulary": sheet.vocabulary,
                "notes": sheet.notes,
                "token": sheet.token,
            },
        },
        "training": {
            "pending": len(pending),
            "pending_names": [p.name for p in pending[:24]],
            "archives": archives,
            "lora": sheet.lora,
        },
        "tuning": sheet.tuning,
        "history": stylelog.read(sheet.home),
    }


def add_style_note(name: str, text: str) -> dict:

    if not text.strip():
        raise errors.Invalid("a note needs some text", field="text")
    sheet = _sheet(name)
    if not sheet.foldered:
        raise errors.Invalid(
            f"'{name}' is a single YAML file, so it has nowhere to keep a history",
            hint=f"move it to styles/{name}/style.yaml")
    stylelog.append(sheet.home, stylelog.note_event(text))
    return {"ok": True, "history": stylelog.read(sheet.home)}


def style_exemplar(name: str, paths: list[str], remove: bool = False) -> dict:
    sheet = _sheet(name)
    if not sheet.foldered:
        raise Invalid(
            f"'{name}' is a single YAML file, so it has no exemplar folder.",
            hint=f"move it to styles/{name}/style.yaml")

    folder = sheet.home / "context" / "exemplars"
    folder.mkdir(parents=True, exist_ok=True)
    touched: list[Path] = []

    for raw in paths:
        source = files_mod.safe_path(raw, allowed_roots())
        if remove:
            target = folder / source.name
            if target.exists():
                target.unlink()
                touched.append(target)
            continue
        if source.parent.resolve() == folder.resolve():
            continue
        target = files_mod.unique_name(folder, files_mod.safe_filename(source.name))
        shutil.copy2(source, target)
        touched.append(target)

    if touched:
        event = stylelog.context_event(
            [] if remove else touched, touched if remove else [], home=sheet.home)
        stylelog.append(sheet.home, event)
    return {"ok": True, "changed": [p.name for p in touched],
            "detail": style_detail(name)}


def style_prompts(name: str, vocabulary: dict | None, notes: str | None) -> dict:

    sheet = _sheet(name)
    changed = []

    if vocabulary is not None:
        if not isinstance(vocabulary, dict):
            raise Invalid("vocabulary must be an object of group -> list",
                          field="vocabulary")
        doc = load_roundtrip(sheet.path)
        clean = {}
        for group, fragments in vocabulary.items():
            if not isinstance(fragments, list):
                raise Invalid(f"vocabulary.{group} must be a list",
                              field=f"vocabulary.{group}")
            kept = [str(f).strip() for f in fragments if str(f).strip()]
            if kept:
                clean[group] = kept
        doc["vocabulary"] = clean
        dump_roundtrip(doc, sheet.path)
        changed.append(f"{len(clean)} vocabulary group(s)")

    if notes is not None:
        if not sheet.foldered:
            raise Invalid(
                f"'{name}' is a single file, so it has nowhere to keep notes.",
                hint=f"move it to styles/{name}/style.yaml")
        sidecar = sheet.home / "context" / "notes.md"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(notes)
        changed.append("notes")

    if changed and sheet.foldered:
        stylelog.append(sheet.home, stylelog.context_event(
            [], home=sheet.home, note=f"edited {', '.join(changed)}"))
    return {"ok": True, "changed": changed, "detail": style_detail(name)}


def palette_list() -> dict:
    from pipeline.looks.palettes import discover

    out = []
    for name, pal in sorted(discover(ROOT).items()):
        out.append({"name": pal.key, "label": name, "size": len(pal.colours),
                    "colours": [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in pal.colours[:64]]})
    return {"palettes": out}


class Looks(BaseRouter):
    prefix = "/api"

    @get("/styles", "every style sheet",
         returns=Shape(styles=list, broken=list))
    def sheets(self, req):
        found = styles.discover(ROOT)
        return {"styles": [s.summary(ROOT) for s in found.values()],
                "broken": [{"path": str(b.path), "why": b.why}
                           for b in styles.broken(ROOT)]}

    @get("/style/detail", "one sheet, with its context and history",
         returns=Shape(name=str, label=str, home=str, foldered=bool,
                       summary=dict, context=dict, history=list,
                       training=dict, tuning=dict))
    def detail(self, req):
        return style_detail(req.required("name"))

    @get("/style/preview", "what a config would actually send",
         returns=Shape(module=str, resolved_prompt=str, conflicts=list,
                       record=dict, palette=str))
    def preview(self, req):
        name = req.required("config")
        path = CONFIGS / f"{name}.yaml"
        if not path.exists():
            raise NotFound("config", name,
                           available=[p.stem for p in CONFIGS.glob("*.yaml")
                                      if not p.stem.startswith("_")])
        cfg = load_roundtrip(path)
        extra = [s for s in req.params.get("with", []) if s]
        if extra:
            cfg = {**cfg, "styles": list(cfg.get("styles") or []) + extra}
        return styles.preview(ROOT, cfg)

    @get("/style/training", "what this sheet has collected for training",
         returns=Shape(dir=str, plan=dict, staged=list, targets=list,
                       verdict=dict))
    def training(self, req):
        from ..looks import training

        sheet = _sheet(req.required("name"))
        return training.preview(sheet.home)

    @post("/style/note", "add a line to a sheet's history",
          returns=Shape(ok=bool, history=list))
    def note(self, req):
        return add_style_note(req.need("name"), req.get("text", ""))

    @post("/style/exemplar", "add or remove reference images",
          returns=Shape(ok=bool, changed=list, detail=dict))
    def exemplar(self, req):
        return style_exemplar(req.need("name"), req.get("paths") or [],
                              bool(req.get("remove")))

    @post("/style/prompts", "edit a sheet's vocabulary",
          returns=Shape(ok=bool, changed=list, detail=dict))
    def prompts(self, req):
        return style_prompts(req.need("name"), req.get("vocabulary"),
                             req.get("notes"))

    @get("/palettes", "every palette, grouped", returns=Shape(palettes=list))
    def palettes(self, req):
        return palette_list()
