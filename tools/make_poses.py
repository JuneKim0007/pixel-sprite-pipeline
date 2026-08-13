#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from pipeline.geometry.bodyspace import NEUTRAL, VIEWS, pose_from, project, resolve_view
from pipeline.geometry.openpose import JOINTS, render

ROOT = Path(__file__).resolve().parent.parent
POSE_DIR = ROOT / "poses"


def p(**overrides) -> dict:
    """NEUTRAL with named joints replaced. Values are (lateral, depth, height)."""
    return pose_from(NEUTRAL, **overrides)


# --------------------------------------------------------------------- poses

IDLE = [p()]

IDLE_BREATHE = [
    p(),
    p(
        neck=(0.0, 0.0, 0.232), nose=(0.0, 0.030, 0.152),
        r_shoulder=(-0.055, 0.0, 0.250), l_shoulder=(0.055, 0.0, 0.250),
        r_elbow=(-0.065, 0.002, 0.358), l_elbow=(0.065, 0.002, 0.358),
        r_wrist=(-0.070, 0.004, 0.458), l_wrist=(0.070, 0.004, 0.458),
        r_eye=(-0.014, 0.026, 0.143), l_eye=(0.014, 0.026, 0.143),
        r_ear=(-0.030, -0.004, 0.149), l_ear=(0.030, -0.004, 0.149),
    ),
]


ATTACK = [
    # 0 — wind up: weight back, weapon arm drawing behind
    p(
        neck=(0.0, -0.020, 0.228), nose=(0.0, 0.012, 0.150),
        l_elbow=(0.060, -0.050, 0.330), l_wrist=(0.050, -0.100, 0.245),
        r_elbow=(-0.062, 0.010, 0.350), r_wrist=(-0.055, 0.030, 0.440),
        r_ankle=(-0.040, -0.050, 0.815), l_ankle=(0.040, 0.040, 0.815),
        r_knee=(-0.038, -0.020, 0.655), l_knee=(0.038, 0.020, 0.655),
    ),
    # 1 — cocked: fully loaded
    p(
        neck=(0.0, -0.030, 0.230), nose=(0.0, 0.004, 0.152),
        l_elbow=(0.060, -0.075, 0.315), l_wrist=(0.050, -0.135, 0.205),
        r_elbow=(-0.062, 0.006, 0.352), r_wrist=(-0.055, 0.026, 0.436),
        r_ankle=(-0.040, -0.060, 0.818), l_ankle=(0.040, 0.048, 0.818),
        r_knee=(-0.038, -0.026, 0.658), l_knee=(0.038, 0.026, 0.658),
    ),
    # 2 — release: arm whips forward
    p(
        neck=(0.0, 0.010, 0.224), nose=(0.0, 0.042, 0.146),
        l_elbow=(0.065, 0.055, 0.300), l_wrist=(0.060, 0.115, 0.225),
        r_elbow=(-0.062, 0.000, 0.350), r_wrist=(-0.055, 0.014, 0.446),
        r_ankle=(-0.040, -0.040, 0.816), l_ankle=(0.040, 0.062, 0.816),
        r_knee=(-0.038, -0.016, 0.656), l_knee=(0.038, 0.034, 0.656),
    ),
    # 3 — mid swing: driving through
    p(
        neck=(0.0, 0.020, 0.228), nose=(0.0, 0.054, 0.150),
        l_elbow=(0.068, 0.095, 0.325), l_wrist=(0.060, 0.175, 0.335),
        r_elbow=(-0.062, -0.004, 0.352), r_wrist=(-0.055, 0.010, 0.450),
        r_ankle=(-0.040, -0.038, 0.818), l_ankle=(0.040, 0.076, 0.818),
        r_knee=(-0.038, -0.014, 0.658), l_knee=(0.038, 0.042, 0.658),
    ),
    # 4 — full extension: the strike has landed
    p(
        neck=(0.0, 0.028, 0.234), nose=(0.0, 0.062, 0.156),
        l_elbow=(0.070, 0.115, 0.350), l_wrist=(0.062, 0.205, 0.415),
        r_elbow=(-0.062, -0.008, 0.358), r_wrist=(-0.055, 0.006, 0.456),
        r_ankle=(-0.040, -0.042, 0.820), l_ankle=(0.040, 0.088, 0.820),
        r_knee=(-0.038, -0.016, 0.660), l_knee=(0.038, 0.048, 0.660),
    ),
    # 5 — follow-through: blade swept low, weight forward
    p(
        neck=(0.0, 0.022, 0.244), nose=(0.0, 0.056, 0.166),
        l_elbow=(0.068, 0.090, 0.380), l_wrist=(0.060, 0.145, 0.485),
        r_elbow=(-0.062, -0.004, 0.366), r_wrist=(-0.055, 0.014, 0.462),
        r_ankle=(-0.040, -0.046, 0.822), l_ankle=(0.040, 0.082, 0.822),
        r_knee=(-0.038, -0.018, 0.664), l_knee=(0.038, 0.044, 0.664),
    ),
]

# Single-frame reaction poses. A hit and a death read from one frame each in
# retro RPGs — the recoil is the whole pose, not a sequence.
HIT = [
    p(
        # Struck: head snapped back, torso recoiling, arms thrown outward.
        neck=(0.0, -0.055, 0.238), nose=(0.0, -0.030, 0.162),
        l_shoulder=(0.058, -0.040, 0.252), r_shoulder=(-0.058, -0.040, 0.252),
        l_elbow=(0.105, -0.020, 0.330), r_elbow=(-0.105, -0.020, 0.330),
        l_wrist=(0.140, 0.030, 0.392), r_wrist=(-0.140, 0.030, 0.392),
        l_hip=(0.035, -0.020, 0.508), r_hip=(-0.035, -0.020, 0.508),
        l_knee=(0.046, 0.010, 0.658), r_knee=(-0.030, -0.030, 0.660),
        l_ankle=(0.052, 0.045, 0.818), r_ankle=(-0.028, -0.055, 0.818),
        l_eye=(0.014, -0.034, 0.153), r_eye=(-0.014, -0.034, 0.153),
        l_ear=(0.030, -0.062, 0.159), r_ear=(-0.030, -0.062, 0.159),
    ),
]

FALL = [
    p(
        # Collapsing: knees buckled, body dropped and folded forward. Not lying
        # flat — a sprite reads as defeated better while still upright-ish.
        neck=(0.0, 0.075, 0.395), nose=(0.0, 0.115, 0.330),
        l_shoulder=(0.052, 0.062, 0.408), r_shoulder=(-0.052, 0.062, 0.408),
        l_elbow=(0.072, 0.095, 0.510), r_elbow=(-0.072, 0.095, 0.510),
        l_wrist=(0.080, 0.140, 0.600), r_wrist=(-0.080, 0.140, 0.600),
        l_hip=(0.035, -0.010, 0.640), r_hip=(-0.035, -0.010, 0.640),
        l_knee=(0.060, 0.075, 0.735), r_knee=(-0.060, 0.075, 0.735),
        l_ankle=(0.048, 0.010, 0.830), r_ankle=(-0.048, 0.010, 0.830),
        l_eye=(0.014, 0.118, 0.322), r_eye=(-0.014, 0.118, 0.322),
        l_ear=(0.030, 0.086, 0.328), r_ear=(-0.030, 0.086, 0.328),
    ),
]

LIBRARY: dict[str, tuple[str, list]] = {
    "idle": ("single neutral stance", IDLE),
    "idle_breathe": ("2-frame looping idle", IDLE_BREATHE),
    "attack": ("6-frame sword swing, payoff on frames 4-5", ATTACK),
    "hit": ("single recoil frame", HIT),
    "fall": ("single collapse frame", FALL),
}


def write_pose(name: str, desc: str, frames: list[dict]) -> Path:
    path = POSE_DIR / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "name": name,
                "description": desc,
                "space": "body",  # (lateral, depth, height); project to view
                "joints": list(JOINTS),
                "frames": [{k: list(v) for k, v in f.items()} for f in frames],
            },
            indent=1,
        )
    )
    return path


def contact_sheet(frames: list[dict], yaw: float, cell: int = 256) -> Image.Image:
    sheet = Image.new("RGB", (cell * len(frames), cell), (0, 0, 0))
    for i, bp in enumerate(frames):
        sheet.paste(render(project(bp, yaw), cell, cell), (i * cell, 0))
    return sheet


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate the pose library.")
    ap.add_argument("--preview", action="store_true", help="write PNG contact sheets")
    ap.add_argument(
        "--views", default="side",
        help="'all', or comma-separated names/angles (default: side)",
    )
    a = ap.parse_args()

    POSE_DIR.mkdir(parents=True, exist_ok=True)
    views = (
        list(VIEWS.items())
        if a.views == "all"
        else [(v.strip(), resolve_view(v.strip())) for v in a.views.split(",")]
    )

    for name, (desc, frames) in LIBRARY.items():
        path = write_pose(name, desc, frames)
        print(f"{path.relative_to(ROOT)}  ({len(frames)} frame(s)) — {desc}")

        if a.preview:
            pdir = POSE_DIR / "preview"
            pdir.mkdir(exist_ok=True)
            for view_name, yaw in views:
                out = pdir / f"{name}__{view_name}.png"
                contact_sheet(frames, yaw).save(out)
                print(f"  {view_name:>20} ({yaw:>5.0f}°) -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
