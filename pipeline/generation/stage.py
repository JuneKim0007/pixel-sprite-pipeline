
from __future__ import annotations

import copy
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping

from ..shared.config import opt
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
    """Everything a stage is given. Two channels, deliberately separate: `artifacts` is what stages pass to each other and what a resume reads back, checkable against requires/produces; `resources` is what the run itself supplies against a stage's `needs`, derived from config and never persisted."""

    root: Path
    outdir: Path
    config: dict[str, Any]
    run_id: str = "run"
    artifacts: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
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

    def need(self, name: str) -> Any:
        """One declared resource. Resolved on first ask and memoised, because a rig under `rig: auto` costs an LLM call."""
        if name not in self.resources:
            from . import resources as _resources

            if name not in _resources.RESOLVERS:
                raise Invalid(f"no resolver for '{name}'",
                              field="needs",
                              hint="declare it in generation/resources.py, or "
                                   "correct the stage's `needs`")
            self.resources[name] = _resources.RESOLVERS[name](self)
        return self.resources[name]

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
    # Artifacts come from other stages; needs come from the run. Both are declared so both can be checked before anything expensive starts.
    needs: ClassVar[frozenset[str]] = frozenset()

    def prepare(self, ctx: Context) -> dict[str, Any]:
        return {}

    @abstractmethod
    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        """Do the work; return the artifacts named in `produces`."""

    def describe(self) -> str:
        req = ", ".join(sorted(self.requires)) or "-"
        if self.optional:
            req += f" (+{', '.join(sorted(self.optional))}?)"
        if self.needs:
            req += f" [{', '.join(sorted(self.needs))}]"
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
