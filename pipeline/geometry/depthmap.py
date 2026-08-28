

from __future__ import annotations

import math
from typing import Mapping, Sequence

from PIL import Image, ImageDraw, ImageFilter

from .bodyspace import frame_fit, frame_scale, project, view_depth

THICKNESS: dict[tuple[str, str], float] = {
    ("neck", "r_hip"): 0.115,
    ("neck", "l_hip"): 0.115,
    ("neck", "r_shoulder"): 0.075,
    ("neck", "l_shoulder"): 0.075,
    ("r_shoulder", "r_elbow"): 0.045,
    ("l_shoulder", "l_elbow"): 0.045,
    ("r_elbow", "r_wrist"): 0.036,
    ("l_elbow", "l_wrist"): 0.036,
    ("r_hip", "r_knee"): 0.055,
    ("l_hip", "l_knee"): 0.055,
    ("r_knee", "r_ankle"): 0.042,
    ("l_knee", "l_ankle"): 0.042,
    ("neck", "nose"): 0.070,
}
HEAD_RADIUS = 0.062


def _capsule(
    draw: ImageDraw.ImageDraw,
    p1: tuple[float, float],
    p2: tuple[float, float],
    width: float,
    shade: int,
) -> None:
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return
    nx, ny = -dy / length * width / 2, dx / length * width / 2
    draw.polygon(
        [
            (p1[0] + nx, p1[1] + ny), (p2[0] + nx, p2[1] + ny),
            (p2[0] - nx, p2[1] - ny), (p1[0] - nx, p1[1] - ny),
        ],
        fill=shade,
    )
    for px, py in (p1, p2):
        r = width / 2
        draw.ellipse([px - r, py - r, px + r, py + r], fill=shade)


def render_depth(
    pose: Mapping[str, Sequence[float]],
    yaw_deg: float,
    width: int = 1024,
    height: int = 1024,
    *,
    near: int = 255,
    far: int = 60,
    blur: float = 6.0,
    depth_scale: float = 1.0,
    lateral_scale: float = 1.0,
    fill: float = 0.0,
    build: float | dict | None = None,
    rig=None,
    props=None,
) -> Image.Image:
    from . import rigs as _rigs

    rig = rig if rig is not None else _rigs.HUMANOID
    # Fit once: projecting with fill= while the prop renderer got the RAW pose drew bodies at 1.3x.
    fitted = frame_fit(pose, fill=fill) if fill else pose
    grow = frame_scale(pose, fill)

    # A dict does it per proportion group, which is what a pot-bellied character with thin arms needs: {torso: 1.6, arms: 0.9}.
    def bulk(parent: str, child: str) -> float:
        if build is None:
            return 1.0
        if isinstance(build, dict):
            from . import rigs as _r

            return float(build.get(_r._group_of(parent, child) or "", 1.0))
        return float(build)
    keypoints = project(
        fitted, yaw_deg, depth_scale=depth_scale, lateral_scale=lateral_scale,
        rig=rig,
    )
    screen = {
        joint: (kp[0] * width, kp[1] * height)
        for joint, kp in zip(rig.joints, keypoints)
        if kp is not None
    }
    depths = view_depth(fitted, yaw_deg)
    if not depths:
        return Image.new("L", (width, height), 0)

    lo, hi = min(depths.values()), max(depths.values())
    span = (hi - lo) or 1.0

    def shade_scalar(value: float) -> int:
        """Depth value -> brightness, for anything not attached to a joint."""
        t = (value - lo) / span
        return int(far + (near - far) * max(0.0, min(1.0, t)))

    def shade(*joints: str) -> int:
        vals = [depths[j] for j in joints if j in depths]
        if not vals:
            return far
        t = (sum(vals) / len(vals) - lo) / span
        return int(far + (near - far) * t)

    canvas = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    scale = min(width, height)

    widths = rig.thickness
    bones = [
        (a, b) for a, b, _ in rig.bones if a in screen and b in screen
    ]
    bones.sort(key=lambda b: (depths.get(b[0], 0) + depths.get(b[1], 0)) / 2)

    for parent, child in bones:
        _capsule(
            draw, screen[parent], screen[child],
            widths[(parent, child)] * scale * grow * bulk(parent, child),
            shade(parent, child),
        )

    head = screen.get(rig.head_joint) or screen.get(rig.root)
    if head:
        r = rig.head_radius * scale * grow * bulk(rig.head_joint, rig.head_joint)
        draw.ellipse(
            [head[0] - r, head[1] - r, head[0] + r, head[1] + r],
            fill=shade(rig.head_joint, rig.root),
        )

    if props:
        from . import props as props_mod

        props_mod.draw_depth(
            draw, props, fitted, rig, yaw_deg, width, height, shade_scalar,
            depth_scale=depth_scale, lateral_scale=lateral_scale, floor=far,
        )

    return canvas.filter(ImageFilter.GaussianBlur(blur)) if blur > 0 else canvas
