"""Asset types: reading them, and writing one from the UI."""

from __future__ import annotations

import re

import yaml

from ..generation import runner
from ..generation.stage import available
from ..shared import modules
from ..shared.errors import Invalid
from .context import ROOT
from .contracts import Shape
from .machine import module_table
from .routing import BaseRouter, get, put

REQUIRED = ("label", "detail", "blurb")


def save_module(name: str, body: dict) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name or ""):
        raise Invalid("an asset type key is lowercase letters, digits and "
                      "underscore, starting with a letter", field="name")

    spec = dict(body.get("module") or {})
    spec.pop("key", None)
    for required in REQUIRED:
        if not str(spec.get(required) or "").strip():
            raise Invalid(f"'{required}' is required", field=required)

    stages = [str(s) for s in (spec.get("stages") or [])]
    if not stages:
        raise Invalid("an asset type needs at least one stage", field="stages")
    spec["stages"] = stages

    extends = str(spec.get("extends") or "")
    if extends:
        if extends == name:
            raise Invalid(f"'{name}' cannot extend itself", field="extends")
        if extends not in modules.all(ROOT):
            raise Invalid(f"no asset type called '{extends}'", field="extends")

    # An order can only be checked once every stage in it exists.
    known = set(available())
    missing = [s for s in stages if s not in known]
    if not missing:
        problem = None
        try:
            runner.validate(runner.build(stages), seeded=set())
        except Exception as e:                                # noqa: BLE001
            problem = str(e)
        if problem and not body.get("force"):
            raise Invalid(problem, field="stages",
                          hint="reorder the stages, or pass force to save it anyway")

    # `key` is the filename, as it is for styles, poses and palettes.
    spec.setdefault("props", True)
    target = modules.directory(ROOT) / f"{name}.yaml"
    target.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"saved": name, "missing": missing, "available": not missing}


class Assets(BaseRouter):
    prefix = "/api"

    @get("/modules", "every asset type, and whether its stages exist",
         returns=Shape(modules=dict))
    def index(self, req):
        return {"modules": module_table()}

    @put("/module", "create or update one asset type",
         returns=Shape(saved=str, missing=list, available=bool))
    def save(self, req):
        return save_module(req.required("name"), req.body)
