

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(line_buffering=True)

from pipeline.orchestration import queue as q  # noqa: E402
from pipeline.shared import paths
from pipeline.shared import settings  # noqa: E402

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

    from pipeline.generation import schema

    cfg_path = paths.resolve(root, "configs") / f"{job.config}.yaml"
    raw = settings.read_yaml(cfg_path)
    schema.apply_overrides(raw, job.data.get("overrides"))

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


def _release(queue: q.Queue, held: list[q.Job]) -> int:
    """Held jobs whose wait is already over, returned to pending."""
    released = 0
    for job in held:
        if q.preflight(ROOT, job).held:
            continue
        queue.move(job, q.PENDING, retry_after=0)
        released += 1
    return released


def _fail(queue: q.Queue, job: q.Job, error: str, message: str, *, detail: str,
          **fields) -> None:
    """Move a job to failed, and put the whole reason beside it on disk."""
    log(f"✗ {message}")
    queue.move(job, q.FAILED, error=error, **fields)
    _write_error(queue.dir(q.FAILED) / job.path.name, detail)


def _tripped(consecutive: int, breaker: int) -> bool:
    if consecutive < breaker:
        return False
    log(f"{consecutive} failures in a row — stopping. Fix and restart.")
    return True


def _await_services(wait: float) -> bool:
    """Block until the services answer. False if a signal arrived first."""
    while not STOPPING:
        ok, why = q.services_up(ROOT)
        if ok:
            return True
        log(f"paused: {why} — rechecking in {wait}s")
        time.sleep(wait)
    return False


def work(args) -> int:
    queue = q.Queue(ROOT)
    reclaim(queue)

    consecutive = 0
    idle = False

    while not STOPPING:
        job = queue.next_ready()

        if job is None:
            held = queue.list(q.HELD)
            if args.drain:
                if _release(queue, held):
                    continue
                log("queue empty — exiting"
                    + (f" ({len(held)} still held)" if held else ""))
                return 0
            if not idle:
                idle = True
                log(f"queue empty ({len(held)} held) — waiting")
            time.sleep(args.poll)
            continue
        idle = False

        check = q.preflight(ROOT, job)
        if check.problems:
            _fail(queue, job, "; ".join(check.problems),
                  f"{job.id}: {check.problems[0]}",
                  detail="\n".join(check.problems))
            consecutive += 1
            if _tripped(consecutive, args.breaker):
                return 1
            continue

        if check.held:
            log(f"⏸ {job.id}: {check.waiting_on[0]} — retrying in {args.hold / 60:.0f}m")
            queue.move(job, q.HELD, retry_after=time.time() + args.hold,
                       error="; ".join(check.waiting_on))
            continue

        if not _await_services(args.service_wait):
            break

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

        # One retry absorbs a transient fault; a second failure is real.
        attempts = job.attempts + 1
        if attempts < args.retries:
            log(f"↻ {job.id} failed, retrying ({attempts}/{args.retries})")
            queue.move(job, q.PENDING, attempts=attempts, error=detail[-500:])
            continue

        consecutive += 1
        _fail(queue, job, detail[-500:],
              f"{job.id} failed after {attempts} attempt(s)",
              detail=detail, attempts=attempts, run_id=run_id)
        if _tripped(consecutive, args.breaker):
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

    ap.add_argument("--timeout", type=float, default=28800,
                    help="seconds before a single job is killed (default 28800, 8h)")
    ap.add_argument("--retries", type=int, default=2,
                    help="attempts per job, so 2 means one retry (default 2)")
    ap.add_argument("--breaker", type=int, default=5,
                    help="consecutive failures before stopping (default 5)")
    a = ap.parse_args()

    if a.status:
        return show_status()

    if a.once:
        a.drain = True          # so an empty queue exits rather than idling
    return work(a)


if __name__ == "__main__":
    raise SystemExit(main())
