"""Reading and writing pipeline configs, comments intact.

A config carries its documentation in comments, and a plain safe_load/safe_dump
cycle deletes all of it - so one Save from the UI would strip the explanation
of every setting it just edited. That is why this round-trips.
"""

from __future__ import annotations

import json
import re

from .. import settings, styles
from ..shared.errors import Invalid, NotFound
from .context import CONFIGS, ROOT, dump_roundtrip, global_cfg, load_roundtrip
from .routing import BaseRouter, get, put
from .runs import validate_order
import yaml


def save_config(name: str, body: dict) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name or ""):
        raise ValueError("invalid config name")
    CONFIGS.mkdir(exist_ok=True)
    target = CONFIGS / f"{name}.yaml"

    if "raw" in body:
        parsed = yaml.safe_load(body["raw"])  # reject invalid YAML first
        problem = validate_order(parsed)
        if problem and not body.get("force"):
            raise ValueError(problem)
        target.write_text(body["raw"])
        return {"saved": name}

    incoming = body.get("config", {}) or {}
    problem = validate_order(incoming)
    if problem and not body.get("force"):
        # Refuse rather than persist an order that cannot run. The runner
        # would catch it later, but only after the user had already lost
        # the working config.
        raise ValueError(problem)

    if target.exists():
        doc = load_roundtrip(target)
        # Explicit resets remove the key so the value falls back to global.
        for dotted in body.get("unset", []) or []:
            settings.unset(doc, dotted)
        changed = _apply_changes(doc, incoming)
        dump_roundtrip(doc, target)
        return {"saved": name, "changed": changed}

    target.write_text(yaml.safe_dump(incoming, sort_keys=False))
    return {"saved": name, "changed": -1}


def save_global(body: dict) -> dict:
    path = settings.global_path(ROOT)
    incoming = body.get("config", {}) or {}
    if path.exists():
        doc = load_roundtrip(path)
        changed = _apply_changes(doc, incoming)
        dump_roundtrip(doc, path)
    else:
        path.write_text(yaml.safe_dump(incoming, sort_keys=False))
        changed = -1
    return {"saved": settings.GLOBAL_NAME, "changed": changed}


def _apply_changes(doc, incoming, path: str = "") -> int:
    """Copy differing leaves from `incoming` into the round-tripped `doc`.

    Writing the whole parsed document back would reformat everything and lose
    the comments; touching only what actually changed leaves the rest of the
    file byte-identical.
    """
    changed = 0
    for key, value in (incoming or {}).items():
        here = f"{path}.{key}" if path else key
        if isinstance(value, dict) and isinstance(doc.get(key), dict):
            changed += _apply_changes(doc[key], value, here)
        elif key not in doc or doc[key] != value:
            doc[key] = value
            changed += 1
    return changed


def _named(name: str):
    """The config called `name`, or a 404 that lists the alternatives."""
    path = CONFIGS / f"{name}.yaml"
    if not name or not path.exists():
        raise NotFound("config", name,
                       available=[p.stem for p in CONFIGS.glob("*.yaml")
                                  if not p.stem.startswith("_")])
    return path


class Configs(BaseRouter):
    prefix = "/api"

    @get("/configs", "every config, with the module each declares")
    def list(self, req):
        CONFIGS.mkdir(exist_ok=True)
        found = sorted(p.stem for p in CONFIGS.glob("*.yaml")
                       if not p.stem.startswith("_"))
        return {"configs": found}

    @get("/config", "one config, and what it resolves to with styles applied")
    def detail(self, req):
        name = req.required("name")
        path = _named(name)
        own = yaml.safe_load(path.read_text()) or {}
        merged, record = styles.layer(ROOT, own)
        return {
            "name": name,
            "module": own.get("module", "animation"),
            "raw": path.read_text(),
            "config": own,                       # what this file pins
            "effective": settings.effective(ROOT, merged),
            "style_record": record,
            "overrides": sorted(settings.overridden_paths(own)),
        }

    @put("/config", "save a config, keeping its comments")
    def save(self, req):
        return save_config(req.required("name"), req.body)

    @get("/global", "the settings every pipeline inherits")
    def globals(self, req):
        return {"config": global_cfg()}

    @put("/global", "save those settings")
    def save_global(self, req):
        return save_global(req.body)
