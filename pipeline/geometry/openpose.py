
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw

from ..shared.errors import Invalid

JOINTS = (
    "nose", "neck",
    "r_shoulder", "r_elbow", "r_wrist",
    "l_shoulder", "l_elbow", "l_wrist",
    "r_hip", "r_knee", "r_ankle",
    "l_hip", "l_knee", "l_ankle",
    "r_eye", "l_eye", "r_ear", "l_ear",
)

LIMBS = (
    (1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7),
    (1, 8), (8, 9), (9, 10), (1, 11), (11, 12), (12, 13),
    (1, 0), (0, 14), (14, 16), (0, 15), (15, 17),
)

# ControlNet was trained against these exact hues - treat as protocol, not decoration.
COLORS = (
    (255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0),
    (85, 255, 0), (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255),
    (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255),
    (255, 0, 255), (255, 0, 170), (255, 0, 85),
)


def _limb_polygon(
    x1: float, y1: float, x2: float, y2: float, width: float
) -> list[tuple[float, float]]:
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    length = math.hypot(x2 - x1, y2 - y1)
    angle = math.atan2(y2 - y1, x2 - x1)
    a, b = length / 2, width / 2

    pts = []
    for i in range(20):
        t = 2 * math.pi * i / 20
        ex, ey = a * math.cos(t), b * math.sin(t)
        pts.append(
            (
                cx + ex * math.cos(angle) - ey * math.sin(angle),
                cy + ex * math.sin(angle) + ey * math.cos(angle),
            )
        )
    return pts


def render(
    keypoints: Sequence[Sequence[float] | None],
    width: int = 1024,
    height: int = 1024,
    thickness: float | None = None,
    rig=None,
) -> Image.Image:
    """For the humanoid rig this is a literal OpenPose control image: 18 joints in COCO order with the exact hues the ControlNet was trained on."""
    from . import rigs as _rigs

    rig = rig if rig is not None else _rigs.HUMANOID
    limbs = rig.limb_pairs
    if len(keypoints) != len(rig.joints):
        raise Invalid(
            f"{rig.name}: expected {len(rig.joints)} keypoints, got {len(keypoints)}",
            field="keypoints",
        )

    # OpenPose's reference stick width is 4px on a 368px canvas; scale it so skeletons look identical whatever resolution we render at.
    stick = thickness if thickness is not None else max(2.0, 4.0 * min(width, height) / 368)

    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    pts: list[tuple[float, float] | None] = [
        None if kp is None else (kp[0] * width, kp[1] * height) for kp in keypoints
    ]

    for i, (a, b) in enumerate(limbs):
        pa, pb = pts[a], pts[b]
        if pa is None or pb is None:
            continue
        draw.polygon(_limb_polygon(*pa, *pb, stick), fill=COLORS[i % len(COLORS)])

    for i, p in enumerate(pts):
        if p is None:
            continue
        r = stick * 0.75
        draw.ellipse(
            [p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=COLORS[i % len(COLORS)]
        )

    return canvas


def load_pose_file(path: Path) -> tuple[str, list[list[list[float] | None]]]:
    """Read a pose file: {"name": ..., "frames": [[[x, y] | null, ...], ...]}."""
    data = json.loads(Path(path).read_text())
    frames = data["frames"]
    for i, frame in enumerate(frames):
        if len(frame) != len(JOINTS):
            raise ValueError(
                f"{path} frame {i}: expected {len(JOINTS)} keypoints, got {len(frame)}"
            )
    return data.get("name", Path(path).stem), frames
