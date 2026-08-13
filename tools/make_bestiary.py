#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.orchestration import queue as q  # noqa: E402

BESTIARY: list[tuple[str, str, str]] = [
    ("goblin",      "humanoid",
     "a snarling goblin with a rusty dagger, ragged leather"),
    ("ogre",        "humanoid_4arm",
     "a hulking four-armed ogre, thick hide, tusks"),
    ("harpy",       "avian",
     "a harpy with feathered wings and taloned feet"),
    ("dire_wolf",   "quadruped",
     "a dire wolf, shaggy grey fur, bared fangs"),
    ("whelp",       "dragon",
     "a young dragon, membranous wings, scaled hide"),
    ("wyvern",      "wyvern",
     "a wyvern, leathery wings for forelimbs, hooked tail"),
    ("cave_spider", "spider",
     "a giant cave spider, eight legs, bristled carapace"),
    ("scorpion",    "scorpion",
     "a desert scorpion, raised stinger, heavy claws"),
    ("serpent",     "serpent",
     "a coiled serpent, banded scales, raised head"),
    ("slime",       "blob",
     "a gelatinous slime, translucent body, wobbling"),
]

ANIMATIONS: list[tuple[str, dict]] = [
    # The payoff of the swing, not the wind-up — frame 4 is full extension.
    ("attack", {"pose.set": [{"name": "attack", "frame": 4,
                              "view": "three_quarter_front"}]}),
    ("idle",   {"pose.name": "idle_breathe", "pose.frames": 2,
                "pose.view": "three_quarter_front"}),
    ("hit",    {"pose.name": "hit", "pose.frames": 1,
                "pose.view": "three_quarter_front"}),
    ("fall",   {"pose.name": "fall", "pose.frames": 1,
                "pose.view": "three_quarter_front"}),
]

SECONDS_PER_FRAME = 210
SECONDS_CANONICAL = 150


def build(monster: str, rig: str, subject: str) -> dict:
    followups = []
    for name, overrides in ANIMATIONS:
        followups.append({
            "config": "monster_anim",
            "name": f"{monster}_{name}",
            "overrides": {"subject": subject, "rig": rig, **overrides},
        })
    return {
        "config": "monster_sheet",
        "name": f"{monster}_sheet",
        "overrides": {"subject": subject, "rig": rig},
        "then": followups,
    }


def estimate() -> tuple[int, float]:
    sheet_frames = 4
    anim_frames = sum(
        len(o.get("pose.set", [])) or o.get("pose.frames", 1)
        for _n, o in ANIMATIONS
    )
    per_monster = (
        SECONDS_CANONICAL + sheet_frames * SECONDS_PER_FRAME     # the sheet
        + len(ANIMATIONS) * SECONDS_CANONICAL                     # per-anim canonical
        + anim_frames * SECONDS_PER_FRAME
    )
    jobs = len(BESTIARY) * (1 + len(ANIMATIONS))
    return jobs, per_monster * len(BESTIARY) / 3600


def main() -> int:
    ap = argparse.ArgumentParser(description="Queue the bestiary.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="*", help="queue only these monsters")
    a = ap.parse_args()

    wanted = [m for m in BESTIARY if not a.only or m[0] in a.only]
    jobs, hours = estimate()
    scaled = hours * len(wanted) / max(len(BESTIARY), 1)

    print(f"\n{len(wanted)} monsters, {len(wanted) * (1 + len(ANIMATIONS))} jobs")
    print(f"rough GPU time: {scaled:.1f} hours\n")
    for name, rig, subject in wanted:
        print(f"  {name:<13} {rig:<16} {subject[:46]}")
    print(f"\n  each -> sheet (4 views) + "
          f"{', '.join(n for n, _ in ANIMATIONS)}")

    if a.dry_run:
        print("\ndry run; nothing queued\n")
        return 0

    queue = q.Queue(ROOT)
    made = 0
    for i, (name, rig, subject) in enumerate(wanted):
        # Priority orders the queue: sheets first so their animations are not
        # left holding for a parent that has not run.
        made += len(queue.submit(build(name, rig, subject), priority=10 + i))
    print(f"\nqueued {made} sheet job(s); animations follow as each one finishes")
    print("start with:  make autopilot\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
