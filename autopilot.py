#!/usr/bin/env python3
"""Run the queue unattended.

    ./autopilot.py                 # work until the queue is empty, then idle
    ./autopilot.py --once          # one job, then exit
    ./autopilot.py --drain         # exit when nothing is left instead of idling
    ./autopilot.py --status        # what is queued, without running anything

Designed around a measurement rather than an intuition. Every stage checks
whether ComfyUI is reachable and gives up in about a millisecond when it is
not, so a loop that simply caught errors and moved on would mark two hundred
jobs failed in under a second if the GPU service died overnight. The guards
below exist to make that impossible:

  a job that cannot work         fails immediately, no retries
  a job that is merely early     is held and retried later
  a service that is down         pauses the worker; no job is blamed
  repeated failures              trip a breaker and stop, leaving the queue

Everything it does is a file move, so killing it at any moment is safe: at
worst one job sits in running/ and is reclaimed on the next start.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(line_buffering=True)

from pipeline import queue as q  # noqa: E402
from pipeline import settings  # noqa: E402

STOPPING = False


def _stop(signum, frame):  # noqa: ARG001
    global STOPPING
    STOPPING = True
    print("\nfinishing the current job, then stopping…")


signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {msg}")


def reclaim(queue: q.Queue) -> None:
    """Put anything left in running/ back to pending after a crash or kill."""
    for job in queue.list(q.RUNNING):
        log(f"reclaiming {job.id} — it was interrupted")
        queue.move(job, q.PENDING)


def run_job(root: Path, job: q.Job, timeout: float) -> tuple[bool, str, str]:
    """Execute one job. Returns (ok, run_id, detail)."""
    import yaml

    from pipeline import schema

    cfg_path = root / "configs" / f"{job.config}.yaml"
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    for path, value in (job.data.get("overrides") or {}).items():
        schema.set_path(raw, path, value)

    runs = settings.resolve_dir(
        root, (settings.load_global(root).get("paths") or {}).get("output_dir"),
        "out/runs")
    run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{job.id}"
    outdir = runs / run_id
    outdir.mkdir(parents=True, exist_ok=True)

    effective = outdir / "config.yaml"
    effective.write_text(yaml.safe_dump(raw, sort_keys=False))

    log_path = outdir / "run.log"
    with log_path.open("w") as fh:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(root / "run.py"), str(effective),
             "--run-id", run_id],
            cwd=root, stdout=fh, stderr=subprocess.STDOUT,
        )
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return False, run_id, f"exceeded the {timeout / 60:.0f} minute limit"

    if code == 0:
        return True, run_id, ""
    tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-25:])
    return False, run_id, tail


def chain(queue: q.Queue, job: q.Job, run_id: str) -> int:
    """Queue whatever this job said should follow it.

    A child inherits by reference — it points at the finished run rather than
    regenerating its canonical, so an animation is guaranteed to match the
    sheet it came from and does not pay two GPU-minutes to rediscover it.
    """
    made = 0
    for spec in job.data.get("then") or []:
        spec = dict(spec)
        overrides = dict(spec.get("overrides") or {})
        if spec.pop("inherit", True):
            overrides.setdefault("references.from_run", run_id)
        spec["overrides"] = overrides
        spec.setdefault("config", job.config)
        made += len(queue.submit(spec, priority=int(spec.get("priority", 40))))
    return made


def work(args) -> int:
    queue = q.Queue(ROOT)
    reclaim(queue)

    consecutive = 0
    idle_since = None

    while not STOPPING:
        job = queue.next_ready()

        if job is None:
            held = len(queue.list(q.HELD))
            if args.drain and not held:
                log("queue empty — exiting")
                return 0
            if idle_since is None:
                idle_since = time.time()
                log(f"queue empty ({held} held) — waiting")
            time.sleep(args.poll)
            continue
        idle_since = None

        # 1. Would this job work at all? Milliseconds, and no GPU touched.
        check = q.preflight(ROOT, job)
        if check.problems:
            log(f"✗ {job.id}: {check.problems[0]}")
            queue.move(job, q.FAILED, error="; ".join(check.problems))
            _write_error(queue.dir(q.FAILED) / job.path.name,
                         "\n".join(check.problems))
            consecutive += 1
            if consecutive >= args.breaker:
                log(f"{consecutive} failures in a row — stopping. Fix and restart.")
                return 1
            continue

        if check.held:
            when = time.time() + args.hold
            log(f"⏸ {job.id}: {check.waiting_on[0]} — retrying in {args.hold / 60:.0f}m")
            queue.move(job, q.HELD, retry_after=when,
                       error="; ".join(check.waiting_on))
            continue

        # 2. Is the machine healthy? A dead service is not the job's fault.
        while not STOPPING:
            ok, why = q.services_up(ROOT)
            if ok:
                break
            log(f"paused: {why} — rechecking in {args.service_wait}s")
            time.sleep(args.service_wait)
        if STOPPING:
            break

        # 3. Run it.
        job = queue.move(job, q.RUNNING, started=time.strftime("%Y-%m-%d %H:%M:%S"))
        log(f"▶ {job.id} ({job.config})")
        started = time.time()
        ok, run_id, detail = run_job(ROOT, job, args.timeout)
        took = time.time() - started

        if ok:
            consecutive = 0
            queued = chain(queue, job, run_id)
            queue.move(job, q.DONE, run_id=run_id, seconds=round(took, 1))
            log(f"✓ {job.id} in {took / 60:.1f}m"
                + (f" — queued {queued} follow-up(s)" if queued else ""))
            if args.once:
                log("--once: one job done — exiting")
                return 0
            continue

        # 4. One retry absorbs a transient fault; a second failure is real.
        attempts = job.attempts + 1
        if attempts < args.retries:
            log(f"↻ {job.id} failed, retrying ({attempts}/{args.retries})")
            queue.move(job, q.PENDING, attempts=attempts, error=detail[-500:])
            continue

        consecutive += 1
        queue.move(job, q.FAILED, attempts=attempts, run_id=run_id,
                   error=detail[-500:])
        _write_error(queue.dir(q.FAILED) / job.path.name, detail)
        log(f"✗ {job.id} failed after {attempts} attempt(s)")
        if consecutive >= args.breaker:
            log(f"{consecutive} failures in a row — stopping. Fix and restart.")
            return 1

    log("stopped")
    return 0


def _write_error(job_path: Path, text: str) -> None:
    job_path.with_suffix(".error.txt").write_text(text + "\n")


def show_status() -> int:
    queue = q.Queue(ROOT)
    counts = queue.all()
    print()
    for state in q.STATES:
        jobs = counts[state]
        print(f"  {state:<8} {len(jobs)}")
        for job in jobs[:6]:
            extra = ""
            if job.get("error"):
                extra = f"  — {job['error'].splitlines()[0][:60]}"
            print(f"      {job['id']}{extra}")
        if len(jobs) > 6:
            print(f"      … {len(jobs) - 6} more")
    print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the sprite queue unattended.")
    ap.add_argument("--once", action="store_true", help="run a single job and exit")
    ap.add_argument("--drain", action="store_true",
                    help="exit when the queue empties instead of idling")
    ap.add_argument("--status", action="store_true", help="show the queue and exit")
    ap.add_argument("--poll", type=float, default=20,
                    help="seconds between checks when idle (default 20)")
    ap.add_argument("--hold", type=float, default=600,
                    help="seconds before retrying a job whose dependency is not "
                         "ready (default 600)")
    ap.add_argument("--service-wait", type=float, default=60,
                    help="seconds between health checks while paused (default 60)")
    ap.add_argument("--timeout", type=float, default=7200,
                    help="seconds before a single job is killed (default 7200)")
    ap.add_argument("--retries", type=int, default=2,
                    help="attempts per job, so 2 means one retry (default 2)")
    ap.add_argument("--breaker", type=int, default=5,
                    help="consecutive failures before stopping (default 5)")
    a = ap.parse_args()

    if a.status:
        return show_status()
    # --once used to set --drain, which means "exit when the queue empties" —
    # so it ran every job in the queue rather than one. The loop now returns
    # after the first job completes; drain still means drain.
    if a.once:
        a.drain = True          # so an empty queue exits rather than idling
    return work(a)


if __name__ == "__main__":
    raise SystemExit(main())
