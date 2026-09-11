
from __future__ import annotations

import copy
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping

from ..shared.config import opt
from ..shared.settings import deep_merge
from ..shared.errors import Invalid, NotFound
from ..shared.registry import Decorated, Registry

__all__ = ["opt", "Resource", "Context", "Stage", "register", "get",
           "available", "defaults_for"]

log = logging.getLogger("pixel.config")


def _set(block: dict[str, Any]) -> dict[str, Any]:
    """A config block with the keys it left blank dropped, at every depth."""
    out: dict[str, Any] = {}
    for key, value in block.items():
        if isinstance(value, dict):
            out[key] = _set(value)
        elif value is not None:
            out[key] = value
    return out


class Resource:
    """Which piece of hardware a stage occupies while it runs."""

    GPU = "gpu"
    CPU = "cpu"
    LLM = "llm"

    PARALLELISABLE = frozenset({CPU})


@dataclass
class Context:
    """Everything a stage is given; only `artifacts` pass between stages."""

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

    def settings(self, path: str) -> Any:
        """The value at one config path: the config, then its asset type, then the field."""
        from .schema import SCHEMA, get_path

        here = get_path(self.config, path)
        field = SCHEMA.field(path)
        if field is not None:
            if here is not None:
                return here
            if field.inherits:
                return self.settings(field.inherits)
            return self._module_default(path, field.default)
        merged = deep_merge(defaults_for(path), _set(here or {}))
        for key, value in self._module_defaults().items():
            if key.startswith(f"{path}.") and get_path(self.config, key) is None:
                _set_path(merged, key[len(path) + 1:], value)
        # A block read does not visit its leaves, so inheritance has to be
        # applied here too or frames would read canonical's only one way in.
        for field in SCHEMA.fields:
            if not (field.inherits and field.key.startswith(f"{path}.")):
                continue
            if get_path(self.config, field.key) is None:
                _set_path(merged, field.key[len(path) + 1:],
                          self.settings(field.inherits))
        return merged

    def _module_defaults(self) -> dict[str, Any]:
        from ..shared import modules

        return modules.defaults_for(self.root, self.config.get("module"))

    def _module_default(self, path: str, fallback: Any) -> Any:
        found = self._module_defaults().get(path)
        return fallback if found is None else found

    def resume_numbering(self) -> None:
        """Continue the NN_stage folder numbering instead of restarting at 00."""
        existing = []
        for d in sorted(self.outdir.iterdir()):
            m = re.match(r"^(\d\d)_(.+)$", d.name) if d.is_dir() else None
            if m:
                existing.append((int(m.group(1)), m.group(2)))
        for index, name in sorted(existing):
            self._order[name] = index

    def stage_dir(self, name: str) -> Path:
        idx = self._order.setdefault(name, len(self._order))
        path = self.outdir / f"{idx:02d}_{name}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def need(self, name: str) -> Any:
        """One declared resource, resolved on first ask and memoised."""
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
    # One vocabulary with `LayerSpec`: a name needed, and a name made available.
    needs: ClassVar[frozenset[str]] = frozenset()
    gives: ClassVar[frozenset[str]] = frozenset()
    # Soft: absent is fine, produced LATER is not.
    optional: ClassVar[frozenset[str]] = frozenset()
    DEFAULTS: ClassVar[dict[str, Any]] = {}

    def prepare(self, ctx: Context) -> dict[str, Any]:
        return {}

    @abstractmethod
    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        """Do the work; return the artifacts named in `gives`."""

    def describe(self) -> str:
        req = ", ".join(sorted(self.needs)) or "-"
        if self.optional:
            req += f" (+{', '.join(sorted(self.optional))}?)"
        pro = ", ".join(sorted(self.gives)) or "-"
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


def _set_path(target: dict, path: str, value: Any) -> None:
    node = target
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def defaults_for(path: str) -> dict[str, Any]:
    """One block's settings before any config touches them."""
    from .schema import SCHEMA

    cls = _REGISTRY.find(path)
    return deep_merge(SCHEMA.defaults_under(path),
                      copy.deepcopy(getattr(cls, "DEFAULTS", {}) or {}) if cls
                      else {})
