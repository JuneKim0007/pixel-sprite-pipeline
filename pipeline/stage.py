"""Stage contract and registry.

A stage is one replaceable step. It declares what it consumes, what it
produces, and which hardware resource it occupies. The runner uses those three
facts to validate any ordering you write in config and to decide what may run
concurrently.

Adding a stage means writing a class and decorating it with @register. Nothing
else in the pipeline needs to know it exists — that is the whole point of the
indirection, and it is what makes stages swappable from config.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


def opt(cfg: dict, key: str, default: Any) -> Any:
    """cfg.get with blank YAML keys treated as absent.

    `size:` with nothing after it parses to None, not to a missing key, so
    dict.get(key, default) hands back None and the default never applies. The
    commented configs are full of intentionally-blank keys meaning "use the
    default", so every read of them has to go through here.
    """
    value = cfg.get(key, None)
    return default if value is None else value


class Resource:
    """Which piece of hardware a stage occupies while it runs.

    This is the parallelism policy, and it is deliberately coarse:

    GPU  One Metal device, and SDXL + ControlNet + IP-Adapter already fill most
         of 16 GB. Two concurrent GPU stages would thrash into swap and run
         slower than running them one after the other. Always serialised.
    CPU  Palette snapping, sheet assembly, skeleton rendering. Genuinely
         parallel, and also parallel *internally* across frames.
    LLM  Ollama, in its own process. Serialised against GPU stages because it
         must be unloaded before SDXL loads — 16 GB does not fit both.
    """

    GPU = "gpu"
    CPU = "cpu"
    LLM = "llm"

    PARALLELISABLE = frozenset({CPU})


@dataclass
class Context:
    """Everything a stage needs, and the shared bag it writes results into.

    `artifacts` is the only channel between stages. A stage reads the keys it
    declared in `requires` and writes the keys it declared in `produces`;
    reaching for anything else breaks the dependency checking that makes
    reordering safe.
    """

    root: Path                      # project root
    outdir: Path                    # this run's directory: out/runs/<run_id>/
    config: dict[str, Any]          # the parsed config file
    run_id: str = "run"
    artifacts: dict[str, Any] = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)
    stopped_at: str | None = None
    _order: dict[str, int] = field(default_factory=dict)

    def stage_config(self, name: str) -> dict[str, Any]:
        """Per-stage settings block, or an empty dict if the file omits it."""
        return self.config.get(name, {}) or {}

    def stage_dir(self, name: str) -> Path:
        """This stage's own output folder, prefixed with its execution index.

        Numbering the folders means the directory listing reproduces the order
        the pipeline actually ran in — which is worth having when the order is
        itself configurable and may differ between runs.
        """
        idx = self._order.setdefault(name, len(self._order))
        path = self.outdir / f"{idx:02d}_{name}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def references(self):
        """The typed reference library, resolved once per run."""
        cached = self.artifacts.get("_refs")
        if cached is not None:
            return cached
        from . import references as refs_mod
        from . import settings as settings_mod

        cfg = dict(self.config.get("references") or {})
        # from_run resolves against wherever runs actually live, which the
        # config may have moved.
        cfg.setdefault("_runs_dir", str(settings_mod.resolve_dir(
            self.root, (self.config.get("paths") or {}).get("output_dir"),
            "out/runs")))
        lib = refs_mod.load(self.root, cfg)

        # Style exemplars may arrive from a style sheet rather than the config.
        for path in self.config.get("references", {}).get("style_exemplars") or []:
            lib.style.append(refs_mod.Reference(
                path=Path(path), role="style", label="exemplar"))

        self.artifacts["_refs"] = lib
        return lib

    def rig(self):
        """The active rig, resolving `rig: auto` once and remembering it.

        Every stage must agree on the topology; resolving independently would
        let the depth stage draw a dragon while the frames stage conditions a
        humanoid. Detection is also expensive enough to be worth doing once.
        """
        cached = self.artifacts.get("_rig")
        if cached is not None:
            return cached
        from . import detect, rigs as _rigs

        rig, record = detect.resolve(self)

        # Proportions come from three places, most explicit last: the rig's
        # own defaults, whatever an annotated reference measures, then the
        # config. Measured beats generic, and a hand-set value beats both.
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
            from . import annotate as ann
        except ImportError:  # pragma: no cover
            return {}

        merged: dict[str, list[float]] = {}
        for a in ann.gather(self.root, self.config.get("references") or {}):
            for group, factor in ann.infer_proportions(a).items():
                merged.setdefault(group, []).append(factor)
        # Median across references: one odd photograph should not redefine the
        # character's build.
        out = {}
        for group, values in merged.items():
            values.sort()
            out[group] = round(values[len(values) // 2], 2)
        return out

    def training_dir(self, kind: str, tier: str = "") -> Path:
        """Corpus folder for a trainable stage, e.g. training/sprite/5_favourite.

        The numeric prefix is kohya's `num_repeats` convention: a folder named
        `5_favourite` feeds each of its images five times per epoch. Weighting
        images is therefore a matter of which folder they sit in, and needs no
        custom training code.
        """
        path = self.root / "training" / kind
        if tier:
            path = path / tier
        path.mkdir(parents=True, exist_ok=True)
        return path

    def require(self, key: str) -> Any:
        if key not in self.artifacts:
            raise KeyError(
                f"artifact '{key}' is missing. Either an earlier stage failed, "
                f"or the stage order in your config puts its producer later."
            )
        return self.artifacts[key]


class Stage(ABC):
    """Base class for every pipeline step."""

    name: ClassVar[str]
    resource: ClassVar[str] = Resource.CPU
    requires: ClassVar[frozenset[str]] = frozenset()
    produces: ClassVar[frozenset[str]] = frozenset()
    # Artifacts used if an earlier stage produced them, ignored otherwise. Lets
    # a stage be dropped from the config without breaking its consumers.
    optional: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def run(self, ctx: Context) -> dict[str, Any]:
        """Do the work; return the artifacts named in `produces`."""

    def describe(self) -> str:
        req = ", ".join(sorted(self.requires)) or "-"
        if self.optional:
            req += f" (+{', '.join(sorted(self.optional))}?)"
        pro = ", ".join(sorted(self.produces)) or "-"
        return f"{self.name:<12} [{self.resource}]  needs: {req:<34} gives: {pro}"


_REGISTRY: dict[str, type[Stage]] = {}


def register(cls: type[Stage]) -> type[Stage]:
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__} must set a class-level `name`")
    if cls.name in _REGISTRY:
        raise ValueError(f"duplicate stage name: {cls.name}")
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> type[Stage]:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise KeyError(f"unknown stage '{name}'. Available: {known}")
    return _REGISTRY[name]


def available() -> dict[str, type[Stage]]:
    return dict(_REGISTRY)
