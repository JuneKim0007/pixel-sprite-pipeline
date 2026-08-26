
from __future__ import annotations

import copy
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping

from ..shared.config import opt
from ..shared import paths
from ..shared.errors import Invalid, NotFound
from ..shared.registry import Decorated, Registry

__all__ = ["opt", "Resource", "Context", "Stage", "register", "get",
           "available", "defaults_for"]

log = logging.getLogger("pixel.config")


class Resource:
    """Which piece of hardware a stage occupies while it runs. GPU/LLM serialise - SDXL+ControlNet+IP-Adapter already fill most of 16 GB."""

    GPU = "gpu"
    CPU = "cpu"
    LLM = "llm"

    PARALLELISABLE = frozenset({CPU})


@dataclass
class Context:
    """Everything a stage needs. `artifacts` is the only channel between stages, so what a stage passes on is checkable against requires/produces - but `references()` and `rig()` still resolve on demand rather than being declared and supplied, which is the service locator `docs/NODES.md` §4 exists to remove."""

    root: Path
    outdir: Path
    config: dict[str, Any]
    run_id: str = "run"
    artifacts: dict[str, Any] = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)
    stopped_at: str | None = None
    _order: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from .schema import SCHEMA

        self.config, notes = SCHEMA.clamp(self.config)
        for note in notes:
            log.warning(note)

    def stage_config(self, name: str) -> dict[str, Any]:
        """The stage's settings, its declared defaults already underneath."""
        block = self.config.get(name, {}) or {}
        set_here = {k: v for k, v in block.items() if v is not None}
        return {**defaults_for(name), **set_here}

    def stage_dir(self, name: str) -> Path:
        idx = self._order.setdefault(name, len(self._order))
        path = self.outdir / f"{idx:02d}_{name}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def references(self):
        """The typed reference library, resolved once per run."""
        cached = self.artifacts.get("_refs")
        if cached is not None:
            return cached
        from ..refs import references as refs_mod
        from ..shared import settings as settings_mod

        cfg = dict(self.config.get("references") or {})
        cfg.setdefault("_name", self.config.get("name") or "")
        cfg.setdefault("_runs_dir", str(settings_mod.resolve_dir(
            self.root, (self.config.get("paths") or {}).get("output_dir"),
            "out/runs")))
        lib = refs_mod.load(self.root, cfg)

        for path in self.config.get("references", {}).get("style_exemplars") or []:
            lib.style.append(refs_mod.Reference(
                path=Path(path), role="style", label="exemplar"))

        self.artifacts["_refs"] = lib
        return lib

    def rig(self):
        cached = self.artifacts.get("_rig")
        if cached is not None:
            return cached
        from ..geometry import rigs as _rigs
        from ..refs import detect

        rig, record = detect.resolve(self)

        measured = self._measured_proportions()
        proportions = {**measured, **(self.config.get("proportions") or {})}
        if measured:
            record["measured_proportions"] = measured
        rig = _rigs.scale(rig, proportions)
        self.artifacts["_rig"] = rig
        self.artifacts["_rig_record"] = record
        return rig

    def _measured_proportions(self) -> dict[str, float]:
        """Limb ratios inferred from annotated references, if any and enabled."""
        if self.config.get("annotate", "skip") == "skip":
            return {}
        try:
            from ..geometry import annotate as ann
        except ImportError:  # pragma: no cover
            return {}

        merged: dict[str, list[float]] = {}
        for a in ann.gather(self.references()):
            for group, factor in ann.infer_proportions(a).items():
                merged.setdefault(group, []).append(factor)
        out = {}
        for group, values in merged.items():
            values.sort()
            out[group] = round(values[len(values) // 2], 2)
        return out

    def training_dir(self, kind: str, tier: str = "") -> Path:
        path = paths.resolve(self.root, "training") / kind
        if tier:
            path = path / tier
        path.mkdir(parents=True, exist_ok=True)
        return path

    def require(self, key: str) -> Any:
        if key not in self.artifacts:
            raise NotFound(
                "artifact", key, available=list(self.artifacts),
                hint="an earlier stage failed, or the stage order in your "
                     "config puts its producer later",
            )
        return self.artifacts[key]


class Stage(ABC):
    """Base class for every pipeline step."""

    name: ClassVar[str]
    resource: ClassVar[str] = Resource.CPU
    requires: ClassVar[frozenset[str]] = frozenset()
    produces: ClassVar[frozenset[str]] = frozenset()
    DEFAULTS: ClassVar[dict[str, Any]] = {}
    optional: ClassVar[frozenset[str]] = frozenset()

    def prepare(self, ctx: Context) -> dict[str, Any]:
        return {}

    @abstractmethod
    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        """Do the work; return the artifacts named in `produces`."""

    def describe(self) -> str:
        req = ", ".join(sorted(self.requires)) or "-"
        if self.optional:
            req += f" (+{', '.join(sorted(self.optional))}?)"
        pro = ", ".join(sorted(self.produces)) or "-"
        return f"{self.name:<12} [{self.resource}]  needs: {req:<34} gives: {pro}"


_SOURCE: Decorated[type[Stage]] = Decorated()
_REGISTRY: Registry[type[Stage]] = Registry("stage", _SOURCE)


def register(cls: type[Stage]) -> type[Stage]:
    if not getattr(cls, "name", None):
        raise Invalid(f"{cls.__name__} must set a class-level `name`")
    return _SOURCE.add(cls.name, cls, what="stage")


def get(name: str) -> type[Stage]:
    return _REGISTRY.get(name)


def available() -> dict[str, type[Stage]]:
    return _REGISTRY.all()


def defaults_for(name: str) -> dict[str, Any]:
    cls = _REGISTRY.find(name)
    return copy.deepcopy(getattr(cls, "DEFAULTS", {}) or {}) if cls else {}
