
from __future__ import annotations
from ..shared import files, paths, settings


import itertools
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from ..shared.errors import Invalid
from . import admission

PENDING, RUNNING, DONE, FAILED, HELD = "pending", "running", "done", "failed", "held"
STATES = (PENDING, RUNNING, DONE, FAILED, HELD)


class QueueError(Invalid):
    """A job cannot be built from what it says."""


@dataclass
class Job:
    path: Path
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return self.path.stem

    @property
    def module(self) -> str:
        return self.data.get("module", settings.DEFAULT_MODULE)

    @property
    def config(self) -> str:
        return self.data.get("config", "")

    @property
    def attempts(self) -> int:
        return int(self.data.get("attempts", 0))

    @property
    def state(self) -> str:
        return self.path.parent.name

    def summary(self) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "module": self.module,
            "config": self.config,
            "attempts": self.attempts,
            "overrides": self.data.get("overrides", {}),
            "matrix_cell": self.data.get("matrix_cell"),
            "needs": self.data.get("needs", []),
            "retry_after": self.data.get("retry_after"),
            "error": self.data.get("error"),
            "run_id": self.data.get("run_id"),
        }


class Queue:
    def __init__(self, root: Path) -> None:
        self.root = paths.resolve(root, "queue")
        for state in STATES:
            (self.root / state).mkdir(parents=True, exist_ok=True)


    def dir(self, state: str) -> Path:
        return self.root / state

    def list(self, state: str) -> list[Job]:
        out = []
        for f in sorted(self.dir(state).glob("*.json")):
            try:
                out.append(Job(f, json.loads(f.read_text())))
            except json.JSONDecodeError:
                self._quarantine(f, "not valid JSON")
        return out

    def all(self) -> dict[str, list[dict]]:
        return {state: [j.summary() for j in self.list(state)] for state in STATES}

    def _quarantine(self, path: Path, why: str) -> None:
        dest = self.dir(FAILED) / path.name
        shutil.move(str(path), dest)
        dest.with_suffix(".error.txt").write_text(f"{why}\n")


    def submit(self, spec: dict, priority: int = 50) -> list[Job]:
        matrix = spec.pop("matrix", None) or {}
        base_name = spec.get("name") or spec.get("config") or "job"

        combos: list[dict] = [{}]
        if matrix:
            keys = list(matrix)
            combos = [
                dict(zip(keys, values))
                for values in itertools.product(*(matrix[k] for k in keys))
            ]

        created = []
        stamp = time.strftime("%Y%m%d_%H%M%S")
        for i, combo in enumerate(combos):
            data = json.loads(json.dumps(spec))
            overrides = dict(data.get("overrides") or {})
            overrides.update(combo)
            data["overrides"] = overrides
            data["created"] = stamp
            data["attempts"] = 0
            if combo:
                data["matrix_cell"] = combo

            suffix = f"_{i:02d}" if len(combos) > 1 else ""
            path = files.unique_name(
                self.dir(PENDING), f"{priority:04d}_{stamp}_{base_name}{suffix}.json")
            path.write_text(json.dumps(data, indent=2))
            created.append(Job(path, data))
        return created

    def move(self, job: Job, state: str, **updates) -> Job:
        job.data.update(updates)
        dest = self.dir(state) / job.path.name
        job.path.write_text(json.dumps(job.data, indent=2))
        shutil.move(str(job.path), dest)
        return Job(dest, job.data)

    def next_ready(self) -> Job | None:
        """Oldest pending job, plus any held job whose retry time has passed."""
        now = time.time()
        for job in self.list(HELD):
            if float(job.data.get("retry_after", 0)) <= now:
                self.move(job, PENDING)

        pending = self.list(PENDING)
        return pending[0] if pending else None


@dataclass
class Preflight:
    ok: bool
    problems: list[str] = field(default_factory=list)
    waiting_on: list[str] = field(default_factory=list)

    @property
    def held(self) -> bool:
        return bool(self.waiting_on) and not self.problems


def _entry_paths(entries) -> list[str]:
    if isinstance(entries, (str, dict)):
        entries = [entries]
    return [e if isinstance(e, str) else e.get("path", "") for e in (entries or [])]


def _missing_config(job: Job, cfg_path: Path) -> list[str]:
    if not job.config:
        return ["job has no 'config'"]
    if not cfg_path.exists():
        return [f"no config '{job.config}' in configs/"]
    return []


def _resolve(root: Path, job: Job, cfg_path: Path) -> tuple[dict, list[str]]:
    """The config as the run would see it, or the reason it cannot be read."""
    import yaml

    from ..generation import schema
    from ..looks import styles

    try:
        raw = settings.read_yaml(cfg_path)
    except yaml.YAMLError as e:
        return {}, [f"config is not valid YAML: {e}"]

    schema.apply_overrides(raw, job.data.get("overrides"))
    try:
        return styles.effective(root, raw)[0], []
    except styles.StyleError as e:
        return {}, [str(e)]


def _await_source_run(root: Path, merged: dict) -> list[str]:
    from_run = (merged.get("references") or {}).get("from_run")
    if not from_run:
        return []
    runs = paths.from_config(root, merged, "output_dir")
    return [] if (runs / from_run).is_dir() else [
        f"run '{from_run}' does not exist yet"]


def _await_free_gpu(root: Path) -> list[str]:
    """A run already going, started by hand or left by an earlier autopilot."""
    from ..shared import guard

    busy = guard.run_in_flight()
    return [f"run '{busy}' is still going"] if busy else []


def _await_annotations(root: Path, merged: dict) -> list[str]:
    if merged.get("annotate") != "require":
        return []
    from ..geometry import annotate as ann

    ref_cfg = merged.get("references") or {}
    found = []
    for role in ("identity", "pose"):
        for rel in _entry_paths(ref_cfg.get(role)):
            image = root / rel
            if rel and image.exists() and not ann.load(image):
                found.append(f"awaiting annotation: {rel}")
    return found


def _await_needs(root: Path, job: Job) -> list[str]:
    return [f"missing file: {dep}" for dep in (job.data.get("needs") or [])
            if not (root / dep).exists()]


def preflight(root: Path, job: Job) -> Preflight:
    cfg_path = paths.resolve(root, "configs") / f"{job.config}.yaml"
    problems = _missing_config(job, cfg_path)
    waiting: list[str] = []

    if not problems:
        merged, unreadable = _resolve(root, job, cfg_path)
        if unreadable:
            return Preflight(False, unreadable)
        problems += admission.problems(root, merged)
        waiting += _await_source_run(root, merged)
        waiting += _await_annotations(root, merged)

    waiting += _await_needs(root, job)
    waiting += _await_free_gpu(root)
    return Preflight(not problems, problems, waiting)


def services_up(root: Path, need_llm: bool = False) -> tuple[bool, str]:
    """Are the services this queue depends on reachable?"""
    from ..generation.comfy import Client
    from ..generation.schema import SCHEMA
    from ..shared.settings import load_global

    host = ((load_global(root).get("comfy") or {}).get("host")
            or SCHEMA.field("comfy.host").default)
    if not Client(host).alive():
        return False, f"ComfyUI unreachable at {host}"

    if need_llm:
        from ..refs.llm import Ollama

        if not Ollama().alive():
            return False, "Ollama unreachable"
    return True, ""
