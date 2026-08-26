"""The queue, and the autopilot that drains it."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..orchestration import queue as queue_lib
from ..shared.errors import Conflict, Invalid, NotFound
from .context import CONFIGS, ROOT, load_roundtrip
from .contracts import Shape
from .routing import BaseRouter, get, post
from .context import runs_dir
from ..shared import errors


_AUTOPILOT: dict[str, Any] = {"proc": None, "started": None}
AUTOPILOT_LOG = "autopilot.log"


def _queue():
    from pipeline.orchestration import queue as q

    return q, q.Queue(ROOT)


def queue_state() -> dict:

    q, queue = _queue()
    out: dict[str, list[dict]] = {}
    for state in q.STATES:
        jobs = []
        for job in queue.list(state):
            summary = job.summary()
            if state == q.PENDING:
                check = q.preflight(ROOT, job)
                summary["preflight"] = {
                    "ok": check.ok,
                    "problems": check.problems,
                    "waiting_on": check.waiting_on,
                    "held": check.held,
                }
            jobs.append(summary)
        out[state] = jobs

    proc = _AUTOPILOT["proc"]
    alive = bool(proc and proc.poll() is None)
    ok, why = q.services_up(ROOT)
    return {
        "states": out,
        "counts": {k: len(v) for k, v in out.items()},
        "autopilot": {"running": alive, "started": _AUTOPILOT["started"]},
        "services": {"ok": ok, "why": why},
        "dir": str(queue.root),
    }


def queue_submit(spec: dict, priority: int = 50) -> dict:
    q, queue = _queue()
    if not spec.get("config"):
        raise errors.Invalid("a job needs a 'config'", field="config")
    if not (CONFIGS / f"{spec['config']}.yaml").exists():
        raise errors.NotFound(
            "config", spec["config"],
            available=[p.stem for p in CONFIGS.glob("*.yaml")
                       if not p.stem.startswith("_")])
    created = queue.submit(dict(spec), priority=int(priority))
    return {"created": [j.id for j in created], "count": len(created)}


def queue_act(job_id: str, action: str) -> dict:
    """Retry, hold or drop one job. Nothing here touches a running job."""
    q, queue = _queue()
    for state in q.STATES:
        for job in queue.list(state):
            if job.id != job_id:
                continue
            if state == q.RUNNING:
                raise errors.Conflict(
                    f"{job_id} is running. Stop the autopilot first — moving a "
                    f"job out from under it would leave two writers on one run.")
            if action == "retry":
                queue.move(job, q.PENDING, attempts=0, error=None)
            elif action == "hold":
                queue.move(job, q.HELD, retry_after=time.time() + 3600)
            elif action == "drop":
                job.path.unlink()
            else:
                raise errors.Invalid(f"unknown queue action '{action}'",
                                     field="action",
                                     hint="retry, hold or drop")
            return {"ok": True, "job": job_id, "action": action}
    raise errors.NotFound("job", job_id)


def autopilot(action: str, args: dict | None = None) -> dict:
    proc = _AUTOPILOT["proc"]
    alive = bool(proc and proc.poll() is None)

    if action == "start":
        if alive:
            return {"running": True, "started": _AUTOPILOT["started"],
                    "note": "already running"}
        cmd = [sys.executable, "-u", str(ROOT / "autopilot.py")]
        for flag in ("drain", "once"):
            if (args or {}).get(flag):
                cmd.append(f"--{flag}")
        log = open(runs_dir().parent / AUTOPILOT_LOG, "a")
        started = subprocess.Popen(cmd, cwd=ROOT, stdout=log,
                                   stderr=subprocess.STDOUT)
        _AUTOPILOT.update(proc=started,
                          started=time.strftime("%Y-%m-%d %H:%M:%S"))
        return {"running": True, "started": _AUTOPILOT["started"]}

    if action == "stop":
        if not alive:
            return {"running": False, "note": "not running"}
        # SIGTERM, which autopilot traps to finish the job it is on rather than abandoning a half-written run directory.
        proc.terminate()
        return {"running": False, "note": "asked to stop after the current job"}

    raise errors.Invalid(f"unknown autopilot action '{action}'",
                         field="action", hint="start or stop")


def autopilot_log(tail: int = 4000) -> str:
    path = runs_dir().parent / AUTOPILOT_LOG
    if not path.exists():
        return ""
    text = path.read_text(errors="replace")
    return text[-tail:]


class Jobs(BaseRouter):
    prefix = "/api/queue"

    @get("", "the queue, with preflight on every pending job",
         returns=Shape(states=dict, counts=dict, autopilot=dict,
                       services=dict, dir=str))
    def state(self, req):
        return queue_state()

    @get("/log", "the autopilot's log", returns=Shape(log=str))
    def log(self, req):
        return {"log": autopilot_log()}

    @post("/submit", "queue one job, or a matrix of them",
          returns=Shape(created=list, count=int))
    def submit(self, req):
        return queue_submit(req.get("spec") or {}, int(req.get("priority", 50)))

    @post("/job", "retry, hold or drop one job",
          returns=Shape(ok=bool, job=str, action=str))
    def act(self, req):
        return queue_act(req.get("id", ""), req.get("action", ""))

    @post("/autopilot", "start or stop the drainer",
          returns=Shape(running=bool))
    def pilot(self, req):
        return autopilot(req.get("action", ""), req.body)
