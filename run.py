#!/usr/bin/env python3
"""Pipeline entry point.

    ./run.py configs/knight_attack.yaml              # run it
    ./run.py configs/knight_attack.yaml --explain    # show the plan, run nothing
    ./run.py --resume 20260809_121504_knight_attack  # continue a gated run
    ./run.py --list-stages                           # what's available

Stage order lives in the config, not here, and the runner validates whatever
order you write before executing anything — so a bad reordering fails in a
second with a readable message rather than ten minutes in with a KeyError.

A config may also gate itself with `pipeline.stop_after: <stage>`, which stops
the run so its output can be inspected or edited (skeletons in the rig editor)
before the expensive stages consume them. `--resume` picks it back up.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Stage progress is only useful if it arrives while the stage is running. Python
# block-buffers stdout when it isn't a terminal, which hides all output until
# the process exits — unhelpful for a ten-minute run, and fatal for anything
# tailing this to show live progress.
sys.stdout.reconfigure(line_buffering=True)

from pipeline import artifacts as artifacts_io  # noqa: E402
from pipeline import runner, settings, stage as stage_mod, stages, styles  # noqa: E402,F401


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text()) or {}
    if "pipeline" not in cfg or "stages" not in cfg["pipeline"]:
        raise SystemExit(
            f"{path} must define pipeline.stages, e.g.\n"
            f"  pipeline:\n    stages: [pose, canonical, frames, palette, export]"
        )
    return cfg


def apply_compute(cfg: dict) -> None:
    """Honour the compute settings before anything imports torch heavily.

    MPS has no way to partition the GPU, so these are the only real levers:
    how much unified memory PyTorch may claim, and how many CPU threads the
    host-side work gets.
    """
    compute = cfg.get("compute") or {}
    watermark = compute.get("mps_high_watermark")
    if watermark is not None:
        os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", str(watermark))
    threads = compute.get("torch_threads")
    if threads:
        os.environ.setdefault("OMP_NUM_THREADS", str(threads))


def seed_stage_numbering(ctx: stage_mod.Context) -> None:
    """Continue the NN_stage folder numbering instead of restarting at 00."""
    existing = []
    for d in sorted(ctx.outdir.iterdir()):
        m = re.match(r"^(\d\d)_(.+)$", d.name) if d.is_dir() else None
        if m:
            existing.append((int(m.group(1)), m.group(2)))
    for index, name in sorted(existing):
        ctx._order[name] = index


def main() -> int:
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
    a = ap.parse_args()

    if a.list_stages:
        print("registered stages:")
        for name, cls in sorted(stage_mod.available().items()):
            print("  " + cls().describe())
        return 0

    # ------------------------------------------------------------- resuming
    seeded: dict = {}
    already: set[str] = set()
    if a.resume:
        base = a.outdir or settings.resolve_dir(
            ROOT, (settings.load_global(ROOT).get("paths") or {}).get("output_dir"),
            "out/runs",
        )
        outdir = base / a.resume
        if not outdir.is_dir():
            raise SystemExit(f"no such run to resume: {outdir}")
        seeded, completed = artifacts_io.load(outdir)
        already = set(completed)
        config_path = outdir / "config.yaml"
        if not config_path.exists():
            raise SystemExit(f"{outdir} has no config.yaml — cannot resume")
        run_id = a.resume
    else:
        if not a.config:
            ap.error("a config file is required (or use --list-stages / --resume)")
        if not a.config.exists():
            raise SystemExit(f"no such config: {a.config}")
        config_path = a.config

    raw_cfg = load_config(config_path)
    # Style sheets sit between the global defaults and the pipeline config, so
    # a named look can set anything that affects the result while the pipeline
    # still overrides it and a single job still overrides everything.
    styled, style_record = styles.layer(
        ROOT, raw_cfg, picks=raw_cfg.get('style_picks'))
    cfg = settings.effective(ROOT, styled)
    if style_record["styles"]:
        print(f"styles: {' + '.join(style_record['styles'])}")
    apply_compute(cfg)

    order = cfg["pipeline"]["stages"]
    built = runner.build(order)

    if a.explain:
        runner.validate(built, seeded=set())
        print(runner.describe(built))
        return 0

    gate = None if a.no_gate else (a.stop_after or (cfg.get("pipeline") or {}).get("stop_after"))

    if not a.resume:
        name = a.name or cfg.get("name") or config_path.stem
        run_id = a.run_id or f"{time.strftime('%Y%m%d_%H%M%S')}_{name}"
        base = a.outdir or settings.resolve_dir(
            ROOT, (cfg.get("paths") or {}).get("output_dir"), "out/runs"
        )
        outdir = base / run_id
        outdir.mkdir(parents=True, exist_ok=True)
        # Snapshot the exact config next to its output. Six months from now the
        # only reliable record of how a sprite was made is the config beside it.
        # A queued job has already materialised its merged config here, so the
        # copy would be onto itself.
        snapshot = outdir / "config.yaml"
        if config_path.resolve() != snapshot.resolve():
            shutil.copy2(config_path, snapshot)

    ctx = stage_mod.Context(
        root=ROOT, outdir=outdir, config=cfg, run_id=run_id, artifacts=seeded
    )
    seed_stage_numbering(ctx)

    print(f"run {run_id}\n{runner.describe(built)}")
    if gate:
        print(f"gate: will stop after '{gate}'")

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
