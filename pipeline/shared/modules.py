"""Asset types: what kind of thing a pipeline makes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .contracts import from_entry
from .errors import Invalid
from .registry import Registry, Scanned

# The asset type a config that names none is.
DEFAULT = "animation"


@dataclass
class ModuleSpec:
    """One asset type, as declared."""

    key: str
    label: str
    detail: str
    blurb: str
    stages: list[str] = field(default_factory=list)
    extends: str = ""
    props: bool = True
    # Settings this type starts from, as dotted paths.
    defaults: dict[str, Any] = field(default_factory=dict)

    def rendered(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "detail": self.detail,
                "blurb": self.blurb, "stages": list(self.stages),
                "extends": self.extends, "props": self.props,
                "defaults": dict(self.defaults)}


BUILTIN: dict[str, dict[str, Any]] = {
    "character_sheet": {
        "label": "Character sheet",
        "detail": "one pose, several angles",
        "blurb": "One reference pose seen from several angles. Usually the "
                 "first thing you make, and the input to an animation.",
        "stages": ["pose", "depth", "canonical", "frames", "palette", "export"],
        "props": False,
        "defaults": {"pose.source": "tpose"},
    },
    "animation": {
        "label": "Animation",
        "detail": "one action, several frames",
        "blurb": "A sequence of frames of one character performing an action.",
        "stages": ["pose", "depth", "canonical", "frames", "softbody",
                   "palette", "export"],
    },
    "tileset": {
        "label": "Tileset",
        "detail": "terrain, 47-blob",
        "blurb": "Top-down terrain tiles that meet their neighbours without a "
                 "seam. A different constraint from a character: a sprite is "
                 "judged on its silhouette, a tile on its edges. Needs two "
                 "stages nothing implements yet - a tile layout in place of a "
                 "skeleton, and an edge pass that makes neighbours agree.",
        "stages": ["tile_pose", "canonical", "frames", "tile_edges",
                   "palette", "export"],
    },
    "object": {
        "label": "Objects",
        "detail": "props, no rig",
        "blurb": "Chests, signposts, trees. Neither a character nor a tile - "
                 "no body plan to pose, but placed on a grid. Needs a stage "
                 "that seats one on its cell, which is what a skeleton does "
                 "for a character and nothing does for a crate.",
        "stages": ["canonical", "frames", "grid_fit", "palette", "export"],
    },
}

_HEADER = ("# What kind of thing a pipeline makes. The rail shows one cell per\n"
           "# file here. `stages` is the order a new pipeline of this type\n"
           "# starts from; naming a stage that does not exist yet is allowed,\n"
           "# and the type stays unavailable until something registers it.\n")


def directory(root: Path) -> Path:
    return paths.resolve(root, "modules")


def seed(root: Path) -> Path:
    """Write the builtin types the first time, as `_global.yaml` is written."""
    base = directory(root)
    for key, body in BUILTIN.items():
        path = base / f"{key}.yaml"
        if not path.exists():
            path.write_text(_HEADER + yaml.safe_dump(body, sort_keys=False))
    return base


def _parse(path: Path) -> tuple[str, ModuleSpec]:
    body = yaml.safe_load(path.read_text()) or {}
    if not isinstance(body, dict):
        raise Invalid(f"{path.name} is not a mapping", hint=str(path))
    spec = from_entry(ModuleSpec, {**body, "key": path.stem}, noun="asset type")
    spec.stages = [str(s) for s in (spec.stages or [])]
    return spec.key, spec


_REGISTRIES: dict[Path, Registry[ModuleSpec]] = {}


def registry(root: Path) -> Registry[ModuleSpec]:
    root = Path(root).resolve()
    found = _REGISTRIES.get(root)
    if found is None:
        seed(root)
        found = Registry("asset type", Scanned(directory(root), ["*.yaml"],
                                               _parse, what="asset type"))
        _REGISTRIES[root] = found
    return found


def all(root: Path) -> dict[str, ModuleSpec]:  # noqa: A001
    return registry(root).all()


def find(root: Path, key: str | None) -> ModuleSpec | None:
    return registry(root).find(key or DEFAULT)


def wants_props(root: Path, key: str | None) -> bool:
    """Whether this type attaches props. Was a string compare in props.py."""
    spec = find(root, key)
    return True if spec is None else spec.props


def defaults_for(root: Path, key: str | None) -> dict[str, Any]:
    """The settings this asset type starts from, inherited types included."""
    out: dict[str, Any] = {}
    seen: set[str] = set()
    while key and key not in seen:
        seen.add(key)
        spec = find(root, key)
        if spec is None:
            break
        for path, value in spec.defaults.items():
            out.setdefault(path, value)
        key = spec.extends
    return out


@dataclass(frozen=True)
class Kind:
    """One asset type, fully resolved: what it is, what it needs, what it has.

    The single construction path. Five accessors used to answer parts of this
    question and the availability check lived in the API layer, so adding a
    type meant knowing which to call in which order.
    """

    key: str
    label: str
    detail: str
    blurb: str
    stages: tuple[str, ...]
    inherits: tuple[str, ...]
    defaults: dict[str, Any]
    props: bool
    missing: tuple[str, ...]

    @property
    def runnable(self) -> bool:
        return not self.missing

    def rendered(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "detail": self.detail,
                "blurb": self.blurb, "stages": list(self.stages),
                "extends": self.inherits[1] if len(self.inherits) > 1 else "",
                "props": self.props, "available": self.runnable,
                "missing": list(self.missing)}


def _inherits(root: Path, key: str | None) -> list[str]:
    """A type and the types it extends, nearest first."""
    known = all(root)
    out: list[str] = []
    seen: set[str] = set()
    here = key or DEFAULT
    while here and here in known and here not in seen:
        seen.add(here)
        out.append(here)
        here = known[here].extends
    if here and here in seen:
        raise Invalid(f"asset type '{key}' extends itself through {out}",
                      field="extends")
    return out




def build(root: Path, key: str | None, known_stages=None) -> Kind:
    """Resolve one asset type. `known_stages` is what the runner can execute;
    without it nothing is reported missing."""
    # registry.get, not find: it raises the specific complaint - which field
    # was misspelt, what types exist - where find() only returns None.
    spec = registry(root).get(key or DEFAULT)
    chain = _inherits(root, key)
    stages = tuple(spec.stages)
    missing = tuple(s for s in stages if s not in known_stages) \
        if known_stages is not None else ()
    return Kind(key=chain[0], label=spec.label, detail=spec.detail,
                blurb=spec.blurb, stages=stages, inherits=tuple(chain),
                defaults=defaults_for(root, key), props=spec.props,
                missing=missing)
