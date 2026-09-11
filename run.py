#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

sys.stdout.reconfigure(line_buffering=True)

from pipeline.orchestration import artifacts as artifacts_io  # noqa: E402
from pipeline import stages  # noqa: E402,F401  (importing registers them)
from pipeline.generation import runner, stage as stage_mod  # noqa: E402
from pipeline.orchestration import admission, launch  # noqa: E402
from pipeline.shared.errors import Invalid  # noqa: E402
from pipeline.looks import styles  # noqa: E402
from pipeline.shared import settings  # noqa: E402


def apply_compute(cfg: dict) -> None:

    compute = cfg.get("compute") or {}
    watermark = compute.get("mps_high_watermark")
    if watermark is not None:
        os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", str(watermark))
    threads = compute.get("torch_threads")
    if threads:
        os.environ.setdefault("OMP_NUM_THREADS", str(threads))


def _parse(argv=None):
    ap = argparse.ArgumentParser(description="Run the sprite pipeline.")
    ap.add_argument("config", nargs="?", type=Path, help="path to a config YAML")
    ap.add_argument("--explain", action="store_true",
                    help="print the validated execution plan and exit")
    ap.add_argument("--list-stages", action="store_true",
                    help="list registered stages and their contracts")
    ap.add_argument("--name", help="override the run name")
    ap.add_argument("--outdir", type=Path, help="override the runs directory")
    ap.add_argument("--run-id", help="use this exact run id instead of a timestamp")
    ap.add_argument("--resume", metavar="RUN_ID",
                    help="continue a gated run, skipping stages it finished")
    ap.add_argument("--stop-after", metavar="STAGE",
                    help="gate the run after this stage (overrides the config)")
    ap.add_argument("--no-gate", action="store_true",
                    help="ignore pipeline.stop_after and run to completion")
    return ap, ap.parse_args(argv)


def _resume(a) -> tuple[Path, Path, dict, set[str]]:
    """A run directory to continue, and what its last attempt left behind."""
    base = a.outdir or settings.resolve_dir(
        ROOT, (settings.load_global(ROOT).get("paths") or {}).get("output_dir"),
        "out/runs",
    )
    outdir = base / a.resume
    if not outdir.is_dir():
        raise SystemExit(f"no such run to resume: {outdir}")
    seeded, completed = artifacts_io.load(outdir)
    config_path = outdir / "config.yaml"
    if not config_path.exists():
        raise SystemExit(f"{outdir} has no config.yaml — cannot resume")
    return outdir, config_path, seeded, set(completed)


def main() -> int:
    ap, a = _parse()

    if a.list_stages:
        print("registered stages:")
        for _name, cls in sorted(stage_mod.available().items()):
            print("  " + cls().describe())
        return 0

    seeded: dict = {}
    already: set[str] = set()
    if a.resume:
        outdir, config_path, seeded, already = _resume(a)
        run_id = a.resume
    else:
        if not a.config:
            ap.error("a config file is required (or use --list-stages / --resume)")
        if not a.config.exists():
            raise SystemExit(f"no such config: {a.config}")
        config_path = a.config

    if a.explain:
        _, cfg, record = launch.effective(ROOT, config_path)
        refused = admission.problems(ROOT, cfg)
        if record["styles"]:
            print(f"styles: {' + '.join(record['styles'])}")
        if refused:
            print("this config cannot run:")
            for line in refused:
                print(f"  {line}")
            return 1
        print(runner.describe(runner.build(cfg["pipeline"]["stages"])))
        return 0

    if a.resume:
        cfg = styles.effective(ROOT, settings.read_yaml(config_path))[0]
    else:
        # The same preparation the Run button and autopilot do, so a run does
        # not depend on which of the three started it.
        try:
            ready = launch.prepare(ROOT, config_path, run_id=a.run_id,
                                   name=a.name, base=a.outdir)
        except Invalid as e:
            raise SystemExit("\n".join(filter(None, [e.message, e.hint])))
        cfg, outdir, run_id = ready.cfg, ready.outdir, ready.run_id
        if ready.styles:
            print(f"styles: {' + '.join(ready.styles)}")

    apply_compute(cfg)

    order = cfg["pipeline"]["stages"]
    built = runner.build(order)

    gate = None if a.no_gate else (a.stop_after or (cfg.get("pipeline") or {}).get("stop_after"))

    ctx = stage_mod.Context(
        root=ROOT, outdir=outdir, config=cfg, run_id=run_id, artifacts=seeded
    )
    ctx.resume_numbering()

    print(f"run {run_id}\n{runner.describe(built)}")
    if gate:
        print(f"gate: will stop after '{gate}'")

    from pipeline.geometry import props as props_mod

    doubled = props_mod.said_twice(cfg, ROOT)
    if doubled:
        print(f"note: {', '.join(doubled)} named in both subject and props; "
              f"the prompt asks for each twice")

    from pipeline.looks import vocabulary as _vocab

    clash = _vocab.backdrop_conflict(
        cfg.get("style") or _vocab.DEFAULT_STYLE,
        _vocab.backdrop_colour((cfg.get("background") or {}) or None))
    if clash:
        print(f"note: style says '{clash}' while the backdrop asks for a "
              f"chroma key; the prompt describes two backgrounds")

    try:
        runner.run(built, ctx, stop_after=gate, skip=already)
    finally:
        artifacts_io.save(ctx.outdir, ctx.artifacts, sorted(set(ctx.completed)))

    try:
        shown = ctx.outdir.relative_to(ROOT)
    except ValueError:
        shown = ctx.outdir  # --outdir may point outside the project
    print(f"\noutput: {shown}")
    if ctx.stopped_at:
        print(f"resume with:  ./run.py --resume {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
