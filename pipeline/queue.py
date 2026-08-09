"""A filesystem job queue, and the rules that keep an unattended run alive.

Jobs are JSON files, and their directory is their state:

    queue/pending/  waiting            0010_knight.json
    queue/running/  exactly one
    queue/done/     finished           + .result.json
    queue/failed/   errored            + .error.txt
    queue/held/     waiting on a dependency that is not ready yet

Directories rather than a database because the point is to write a night's work
by hand: `ls queue/pending | wc -l` answers how much is left, and moving a file
between folders is an atomic, crash-safe state transition.

The design is shaped by one measurement. Every stage checks whether ComfyUI is
reachable and fails in about a millisecond when it is not — so if the GPU
service dies at 3am, a naive "catch the error, take the next job" loop burns two
hundred queued jobs in under a second and the night is gone. Four guards follow
from that:

  pre-flight     config errors are caught before a job is dequeued, in
                 milliseconds, without touching the GPU
  health gate    a missing service makes the worker WAIT, never fail a job;
                 "the machine is broken" is not "this job is broken"
  circuit break  consecutive failures pause the queue instead of draining it
  held state     a job whose dependency has not finished yet is not a failure,
                 it is early — so it goes back with a retry time
"""

from __future__ import annotations

import itertools
import json
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PENDING, RUNNING, DONE, FAILED, HELD = "pending", "running", "done", "failed", "held"
STATES = (PENDING, RUNNING, DONE, FAILED, HELD)


class QueueError(RuntimeError):
    pass


@dataclass
class Job:
    path: Path
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return self.path.stem

    @property
    def module(self) -> str:
        return self.data.get("module", "animation")

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
            # Which matrix combination this job is, when it came from one. The
            # overrides carry it too, but mixed in with the job's own.
            "matrix_cell": self.data.get("matrix_cell"),
            "needs": self.data.get("needs", []),
            "retry_after": self.data.get("retry_after"),
            "error": self.data.get("error"),
            "run_id": self.data.get("run_id"),
        }


class Queue:
    def __init__(self, root: Path) -> None:
        self.root = root / "queue"
        for state in STATES:
            (self.root / state).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- reading

    def dir(self, state: str) -> Path:
        return self.root / state

    def list(self, state: str) -> list[Job]:
        out = []
        for f in sorted(self.dir(state).glob("*.json")):
            try:
                out.append(Job(f, json.loads(f.read_text())))
            except json.JSONDecodeError:
                # A half-written file is not a reason to stop; quarantine it.
                self._quarantine(f, "not valid JSON")
        return out

    def all(self) -> dict[str, list[dict]]:
        return {state: [j.summary() for j in self.list(state)] for state in STATES}

    def _quarantine(self, path: Path, why: str) -> None:
        dest = self.dir(FAILED) / path.name
        shutil.move(str(path), dest)
        dest.with_suffix(".error.txt").write_text(f"{why}\n")

    # ------------------------------------------------------------- writing

    def submit(self, spec: dict, priority: int = 50) -> list[Job]:
        """Write a job, expanding `matrix` into one file per combination.

        The matrix is what makes a queue writable by hand: three views crossed
        with two seeds is six jobs from one file, and a night's work fits on a
        page instead of in fifty near-identical documents.
        """
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
            data = json.loads(json.dumps(spec))  # deep copy
            overrides = dict(data.get("overrides") or {})
            overrides.update(combo)
            data["overrides"] = overrides
            data["created"] = stamp
            data["attempts"] = 0
            if combo:
                data["matrix_cell"] = combo

            suffix = f"_{i:02d}" if len(combos) > 1 else ""
            path = self.dir(PENDING) / f"{priority:04d}_{stamp}_{base_name}{suffix}.json"
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


# ---------------------------------------------------------------- pre-flight


@dataclass
class Preflight:
    ok: bool
    problems: list[str] = field(default_factory=list)
    waiting_on: list[str] = field(default_factory=list)

    @property
    def held(self) -> bool:
        return bool(self.waiting_on) and not self.problems


def preflight(root: Path, job: Job) -> Preflight:
    """Check a job without running it.

    Separates two failures that look identical to a naive handler: a config
    that can never work, and a dependency that has not been produced yet. The
    first should fail immediately, the second should wait.
    """
    problems: list[str] = []
    waiting: list[str] = []

    cfg_path = root / "configs" / f"{job.config}.yaml"
    if not job.config:
        problems.append("job has no 'config'")
    elif not cfg_path.exists():
        problems.append(f"no config '{job.config}' in configs/")

    if not problems:
        import yaml

        # `stages` must be imported for its side effect: stage classes register
        # themselves on import, and without it the registry is empty and every
        # job fails validation — turning the guard into the cascade it exists
        # to prevent.
        from . import runner, schema, settings, stages, styles  # noqa: F401

        try:
            raw = yaml.safe_load(cfg_path.read_text()) or {}
        except yaml.YAMLError as e:
            return Preflight(False, [f"config is not valid YAML: {e}"])

        for path, value in (job.data.get("overrides") or {}).items():
            schema.set_path(raw, path, value)
        # A style sheet can set anything, so it has to be applied before the
        # config is validated — and a job naming a missing sheet should fail
        # here, in milliseconds, not after the GPU has warmed up.
        try:
            styled, _record = styles.layer(root, raw)
        except styles.StyleError as e:
            return Preflight(False, [str(e)])
        merged = settings.effective(root, styled)

        order = (merged.get("pipeline") or {}).get("stages") or []
        try:
            runner.validate(runner.build(list(order)), seeded=set())
        except Exception as e:
            problems.append(str(e).split("\n")[0])

        rig = merged.get("rig")
        if rig and rig != "auto":
            from . import rigs

            if rig not in rigs.REGISTRY:
                problems.append(f"unknown rig '{rig}'")

        ref_cfg = merged.get("references") or {}
        if "images" in ref_cfg:
            problems.append(
                "references.images was replaced by typed roles: identity, "
                "style, pose, palette"
            )
        from . import references as refs_mod

        for role in refs_mod.ROLES:
            entries = ref_cfg.get(role) or []
            if isinstance(entries, (str, dict)):
                entries = [entries]
            for entry in entries:
                rel = entry if isinstance(entry, str) else entry.get("path", "")
                if rel and not (root / rel).exists():
                    problems.append(f"references.{role} missing: {rel}")

        from_run = ref_cfg.get("from_run")
        if from_run:
            runs = settings.resolve_dir(
                root, (merged.get("paths") or {}).get("output_dir"), "out/runs")
            if not (runs / from_run).is_dir():
                waiting.append(f"run '{from_run}' does not exist yet")

        # `annotate: require` is a deliberate hold, not a failure: the job is
        # waiting for a person to mark up its references.
        if merged.get("annotate") == "require":
            from . import annotate as ann

            for role in ("identity", "pose"):
                for entry in (ref_cfg.get(role) or []):
                    rel = entry if isinstance(entry, str) else entry.get("path", "")
                    image = root / rel
                    if rel and image.exists() and not ann.load(image):
                        waiting.append(f"awaiting annotation: {rel}")

    for dep in job.data.get("needs") or []:
        if not (root / dep).exists():
            waiting.append(f"missing file: {dep}")

    return Preflight(not problems, problems, waiting)


def services_up(root: Path, need_llm: bool = False) -> tuple[bool, str]:
    """Are the services this queue depends on reachable?"""
    from .comfy import Client
    from .settings import load_global

    host = (load_global(root).get("comfy") or {}).get("host", "http://127.0.0.1:8188")
    if not Client(host).alive():
        return False, f"ComfyUI unreachable at {host}"

    if need_llm:
        from .llm import Ollama

        if not Ollama().alive():
            return False, "Ollama unreachable"
    return True, ""
