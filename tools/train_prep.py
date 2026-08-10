#!/usr/bin/env python3
"""Curate a weighted training corpus for LoRA fine-tuning.

The three things you asked for map onto one mechanism plus one convention:

  "only train on images I liked"      -> you add files explicitly; nothing is
                                         swept in automatically
  "different weights per image"       -> kohya reads a folder named `5_hero` as
                                         "repeat every image in here 5 times per
                                         epoch". Repeats ARE the weight, so
                                         weighting is a matter of which folder
                                         an image lands in. No custom training
                                         code, works with stock kohya.
  "weight recent data more"           -> `--boost` adds repeats on top of the
                                         tier, so a late batch can outweigh an
                                         earlier one without discarding it.

For a stronger version of recency, train a separate LoRA on the new data and
merge with explicit ratios instead:

    python sdxl_merge_lora.py --models old.safetensors new.safetensors \\
                              --ratios 0.7 0.4 --save_to merged.safetensors

That keeps each training run as an independent artifact you can re-weight or
roll back, rather than one adapter that silently drifts.

Usage
    tools/train_prep.py add out/runs/*/03_palette/*_px.png --tier hero
    tools/train_prep.py add new/*.png --tier good --boost 2 --caption "knight"
    tools/train_prep.py status
    tools/train_prep.py config > training/sprite/dataset_config.toml
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Tier -> base repeats. Higher repeats = the trainer sees those images more
# often per epoch = stronger influence on the adapter.
TIERS: dict[str, int] = {
    "hero": 5,     # the handful that define the look
    "good": 3,
    "okay": 1,     # filler for variety; keep influence low
}


def tier_dir(kind: str, tier: str, repeats: int) -> Path:
    return ROOT / "training" / kind / f"{repeats}_{tier}"


def cmd_add(a: argparse.Namespace) -> int:
    if a.tier not in TIERS:
        raise SystemExit(f"unknown tier '{a.tier}'. Choose from: {', '.join(TIERS)}")

    repeats = TIERS[a.tier] + a.boost
    dst_dir = tier_dir(a.kind, a.tier, repeats)
    dst_dir.mkdir(parents=True, exist_ok=True)

    files = [p for p in a.files if p.is_file()]
    if not files:
        raise SystemExit("no input files matched")

    added = 0
    for src in files:
        dst = dst_dir / src.name
        if dst.exists() and not a.overwrite:
            print(f"  skip (exists): {dst.name}")
            continue
        shutil.copy2(src, dst)
        # kohya pairs each image with a same-named .txt caption.
        if a.caption:
            dst.with_suffix(".txt").write_text(a.caption.strip() + "\n")
        added += 1

    log = ROOT / "training" / a.kind / "provenance.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        for src in files:
            fh.write(
                json.dumps(
                    {
                        "added": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "source": str(src),
                        "tier": a.tier,
                        "repeats": repeats,
                        "caption": a.caption or None,
                    }
                )
                + "\n"
            )

    print(f"added {added} file(s) -> {dst_dir.relative_to(ROOT)} (repeats={repeats})")
    return 0


def _scan(kind: str) -> list[tuple[Path, int, int]]:
    base = ROOT / "training" / kind
    if not base.exists():
        return []
    rows = []
    for d in sorted(base.iterdir()):
        if not d.is_dir() or "_" not in d.name:
            continue
        head = d.name.split("_", 1)[0]
        if not head.isdigit():
            continue
        images = [p for p in d.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
        rows.append((d, int(head), len(images)))
    return rows


def cmd_status(a: argparse.Namespace) -> int:
    rows = _scan(a.kind)
    if not rows:
        print(f"training/{a.kind}/ is empty. Add images with `train_prep.py add`.")
        return 0

    print(f"training/{a.kind}/")
    total_images = total_steps = 0
    for d, repeats, count in rows:
        eff = repeats * count
        total_images += count
        total_steps += eff
        print(f"  {d.name:<20} {count:>4} images x{repeats}  = {eff:>5} steps/epoch")

    uncaptioned = sum(
        1
        for d, _, _ in rows
        for p in d.iterdir()
        if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
        and not p.with_suffix(".txt").exists()
    )
    print(f"  {'total':<20} {total_images:>4} images      = {total_steps:>5} steps/epoch")
    if uncaptioned:
        print(f"\n  warning: {uncaptioned} image(s) have no .txt caption")
    if total_images < 20:
        print(
            f"\n  note: {total_images} images is thin for a character LoRA. "
            f"~20+ of the same subject is the usual floor before identity "
            f"locks in rather than the style bleeding."
        )
    return 0


def cmd_config(a: argparse.Namespace) -> int:
    rows = _scan(a.kind)
    if not rows:
        raise SystemExit(f"nothing in training/{a.kind}/ to configure")

    alpha = a.alpha if a.alpha is not None else max(1, a.rank // 2)
    lines = [
        "# Generated by tools/train_prep.py — kohya dataset config.",
        "#",
        "# Training happens elsewhere: SDXL LoRA wants ~12-24GB of VRAM and this",
        "# project targets a 16GB Mac whose inference already fills it. Rent a",
        "# GPU, run kohya sd-scripts against this file, bring the .safetensors",
        "# back into ComfyUI/models/loras/ and name it in models.character_lora",
        "# or models.style_lora.",
        "#",
        f"#   --network_dim {a.rank}   --network_alpha {alpha}",
        f"#   --max_train_steps {a.steps}   --learning_rate {a.lr}",
        "#",
        "# network_dim is CAPACITY, not image count. 8 on twenty images learns a",
        "# style; 32 memorises those twenty images, poses included.",
        "",
        "# num_repeats is the per-folder weight: images in a 5_ folder are seen",
        "# five times per epoch, images in a 1_ folder once.",
        "",
        "[general]",
        "enable_bucket = true",
        f"resolution = {a.resolution}",
        "caption_extension = '.txt'",
        "",
        "[[datasets]]",
        f"batch_size = {a.batch_size}",
        "",
    ]
    for d, repeats, count in rows:
        lines += [
            f"  # {count} image(s)",
            "  [[datasets.subsets]]",
            f"  image_dir = '{d.resolve()}'",
            f"  num_repeats = {repeats}",
            "",
        ]
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Curate a weighted LoRA training corpus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--kind", default="sprite",
        help="corpus name under training/ (default: sprite)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="copy images into a weighted tier")
    p_add.add_argument("files", nargs="+", type=Path)
    p_add.add_argument("--tier", default="good", help=f"one of: {', '.join(TIERS)}")
    p_add.add_argument(
        "--boost", type=int, default=0,
        help="extra repeats on top of the tier — use for recent batches",
    )
    p_add.add_argument("--caption", help="caption text written beside each image")
    p_add.add_argument("--overwrite", action="store_true")
    p_add.set_defaults(func=cmd_add)

    p_status = sub.add_parser("status", help="show the corpus and effective weights")
    p_status.set_defaults(func=cmd_status)

    p_cfg = sub.add_parser("config", help="emit a kohya dataset_config.toml")
    p_cfg.add_argument("--resolution", default="1024,1024")
    p_cfg.add_argument("--batch-size", type=int, default=1)
    # Written into the emitted file as a comment block so a run on a rented GPU
    # is reproducible FROM THE REPO rather than from whatever was typed into a
    # web form and forgotten. Rank is capacity, NOT dataset size: 8 on twenty
    # images learns a style, 32 memorises those twenty images and their poses.
    p_cfg.add_argument("--rank", type=int, default=8,
                       help="LoRA network_dim. 8 is the cautious default (default 8)")
    p_cfg.add_argument("--alpha", type=int, default=None,
                       help="network_alpha; defaults to rank/2")
    p_cfg.add_argument("--steps", type=int, default=1800,
                       help="max_train_steps (default 1800)")
    p_cfg.add_argument("--lr", default="1e-4", help="learning rate (default 1e-4)")
    p_cfg.set_defaults(func=cmd_config)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
