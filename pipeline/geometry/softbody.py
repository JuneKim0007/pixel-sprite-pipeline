

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from scipy.ndimage import map_coordinates

from .bodyspace import NEUTRAL, project_point
from ..shared import contracts
from ..shared.errors import NotFound


@dataclass
class SoftNode:
    """A lump of loose mass attached to a skeleton joint."""

    name: str
    anchor: str = "neck"
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius: float = 0.12
    stiffness: float = 90.0
    damping: float = 6.0
    mass: float = 1.0
    max_displacement: float = 0.03
    influence: float = 1.0
    axis: tuple[float, float] = (1.0, 1.0)

    @classmethod
    def from_config(cls, entry: dict) -> "SoftNode":
        return contracts.from_entry(cls, entry, noun="soft node",
                                    tuples=("offset", "axis"))


@dataclass
class Track:
    """A node's per-frame displacement, in canvas fractions."""

    node: SoftNode
    anchors: list[tuple[float, float]] = field(default_factory=list)
    offsets: list[tuple[float, float]] = field(default_factory=list)


def anchor_positions(
    node: SoftNode, entries: Sequence[dict], *, depth_scale: float = 1.0,
    lateral_scale: float = 1.0,
) -> list[tuple[float, float]]:
    """Where the node sits each frame if it were rigidly attached."""
    out = []
    for entry in entries:
        pose = entry["pose"]
        base = pose.get(node.anchor) or NEUTRAL.get(node.anchor)
        if base is None:
            raise NotFound(f"joint that soft node '{node.name}' anchors to",
                           node.anchor, available=list(NEUTRAL))
        point = tuple(base[i] + node.offset[i] for i in range(3))
        out.append(
            project_point(
                point, entry["yaw"],
                depth_scale=depth_scale, lateral_scale=lateral_scale,
            )
        )
    return out


def simulate(
    node: SoftNode,
    anchors: Sequence[tuple[float, float]],
    *,
    fps: float = 12.0,
    loop: bool = True,
    preroll: int = 2,
    substeps: int = 8,
) -> list[tuple[float, float]]:
    if not anchors:
        return []

    dt = 1.0 / (fps * substeps)
    k, c, m = node.stiffness, node.damping, max(node.mass, 1e-6)

    px, py = anchors[0]
    vx = vy = 0.0
    passes = (preroll + 1) if loop else 1
    recorded: list[tuple[float, float]] = []

    for _pass in range(passes):
        frame_offsets: list[tuple[float, float]] = []
        for ax, ay in anchors:
            # Substep the integration: a stiff spring at 12fps is unstable with one Euler step per frame and will oscillate out of control.
            for _ in range(substeps):
                fx = k * (ax - px) - c * vx
                fy = k * (ay - py) - c * vy
                vx += (fx / m) * dt
                vy += (fy / m) * dt
                px += vx * dt
                py += vy * dt

            dx, dy = (px - ax) * node.axis[0], (py - ay) * node.axis[1]
            limit = node.max_displacement
            mag = math.hypot(dx, dy)
            if mag > limit and mag > 0:
                dx, dy = dx * limit / mag, dy * limit / mag
            frame_offsets.append((dx * node.influence, dy * node.influence))
        recorded = frame_offsets

    return recorded


def build_tracks(
    nodes: Sequence[SoftNode],
    entries: Sequence[dict],
    *,
    fps: float = 12.0,
    loop: bool = True,
    preroll: int = 2,
    depth_scale: float = 1.0,
    lateral_scale: float = 1.0,
) -> list[Track]:
    tracks = []
    for node in nodes:
        if node.influence == 0:
            continue
        anchors = anchor_positions(
            node, entries, depth_scale=depth_scale, lateral_scale=lateral_scale
        )
        tracks.append(
            Track(node=node, anchors=anchors,
                  offsets=simulate(node, anchors, fps=fps, loop=loop, preroll=preroll))
        )
    return tracks


def _falloff(dist: np.ndarray, radius: float) -> np.ndarray:
    t = np.clip(1.0 - dist / max(radius, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def warp_frame(
    image: np.ndarray, tracks: Sequence[Track], frame_index: int
) -> np.ndarray:
    """Displace pixels around each soft node. `image` is HxWxC uint8."""
    h, w = image.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    src_x, src_y = xx.copy(), yy.copy()
    touched = False

    for track in tracks:
        if frame_index >= len(track.offsets):
            continue
        dx, dy = track.offsets[frame_index]
        if dx == 0.0 and dy == 0.0:
            continue
        cx, cy = track.anchors[frame_index]
        cx, cy = cx * w, cy * h
        radius = track.node.radius * min(w, h)

        dist = np.hypot(xx - cx, yy - cy)
        weight = _falloff(dist, radius)
        if not weight.any():
            continue
        src_x -= weight * dx * w
        src_y -= weight * dy * h
        touched = True

    if not touched:
        return image

    out = np.empty_like(image)
    coords = np.stack([src_y, src_x])
    for ch in range(image.shape[2]):
        out[..., ch] = map_coordinates(
            image[..., ch], coords, order=1, mode="nearest"
        ).astype(image.dtype)
    return out


def describe(tracks: Sequence[Track]) -> str:
    if not tracks:
        return "no active soft nodes"
    parts = []
    for t in tracks:
        peak = max((math.hypot(*o) for o in t.offsets), default=0.0)
        parts.append(f"{t.node.name} peak {peak * 100:.1f}% canvas")
    return "; ".join(parts)
