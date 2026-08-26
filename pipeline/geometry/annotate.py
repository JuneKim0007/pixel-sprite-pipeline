

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import rigs

SUFFIX = ".rig.json"


@dataclass
class Annotation:
    image: Path
    rig: str = "humanoid"
    points: dict[str, list[float]] = field(default_factory=dict)
    note: str = ""

    @property
    def placed(self) -> list[str]:
        return sorted(self.points)

    def missing(self) -> list[str]:
        """Joints of this rig that were not placed — cropped, hidden, or skipped."""
        return [j for j in rigs.get(self.rig).joints if j not in self.points]

    def as_dict(self) -> dict:
        return {
            "image": str(self.image),
            "rig": self.rig,
            "points": self.points,
            "note": self.note,
            "placed": len(self.points),
            "missing": self.missing(),
        }


def sidecar_for(image: Path) -> Path:
    return image.with_suffix(image.suffix + SUFFIX)


def load(image: Path) -> Annotation | None:
    path = sidecar_for(image)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return Annotation(
        image=image,
        rig=data.get("rig", "humanoid"),
        points={k: [float(v[0]), float(v[1])] for k, v in (data.get("points") or {}).items()
                if isinstance(v, (list, tuple)) and len(v) == 2},
        note=data.get("note", ""),
    )


def save(annotation: Annotation) -> Path:
    path = sidecar_for(annotation.image)
    path.write_text(json.dumps({
        "rig": annotation.rig,
        "points": annotation.points,
        "note": annotation.note,
    }, indent=1))
    return path


def render(annotation: Annotation, width: int = 1024, height: int = 1024,
           thickness: float | None = None):
    from PIL import Image, ImageDraw

    rig = rigs.get(annotation.rig)
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    stick = thickness if thickness is not None else max(2.0, 4.0 * min(width, height) / 368)

    for i, (a, b, _w) in enumerate(rig.bones):
        pa, pb = annotation.points.get(a), annotation.points.get(b)
        if not pa or not pb:
            continue
        colour = rig.color(i)
        draw.line(
            [pa[0] * width, pa[1] * height, pb[0] * width, pb[1] * height],
            fill=colour, width=int(stick * 2),
        )

    for joint, point in annotation.points.items():
        idx = rig.joints.index(joint) if joint in rig.joints else 0
        r = stick * 0.9
        x, y = point[0] * width, point[1] * height
        draw.ellipse([x - r, y - r, x + r, y + r], fill=rig.color(idx))

    return canvas


def infer_view(annotation: Annotation) -> tuple[float, float]:
    rig = rigs.get(annotation.rig)
    pts = annotation.points

    pairs = [("l_shoulder", "r_shoulder"), ("l_hip", "r_hip"),
             ("l_front_shoulder", "r_front_shoulder")]
    spread = None
    for left, right in pairs:
        if left in pts and right in pts:
            spread = abs(pts[left][0] - pts[right][0])
            reference = abs(rig.neutral[left][0] - rig.neutral[right][0]) if (
                left in rig.neutral and right in rig.neutral) else None
            if reference:
                break
    if spread is None or not reference:
        return 0.0, 0.0

    ratio = max(0.0, min(1.0, spread / reference))
    yaw = math.degrees(math.acos(ratio))  # 0 = front-on, 90 = profile.

    facing_away = not any(j in pts for j in rig.face_joints)
    if facing_away:
        yaw = 180.0 - yaw
    confidence = min(1.0, len(pts) / max(len(rig.joints) * 0.4, 1))
    return round(yaw, 1), round(confidence, 2)


def infer_proportions(annotation: Annotation) -> dict[str, float]:
    rig = rigs.get(annotation.rig)
    pts = annotation.points
    if len(pts) < 4:
        return {}

    anchor = None
    for top, bottom in (("neck", "l_hip"), ("neck", "r_hip"), ("chest", "hips")):
        if top in pts and bottom in pts and top in rig.neutral and bottom in rig.neutral:
            observed = math.dist(pts[top], pts[bottom])
            expected = math.dist(rig.neutral[top][:3:2], rig.neutral[bottom][:3:2])
            if observed > 1e-6 and expected > 1e-6:
                anchor = observed / expected
                break
    if not anchor:
        return {}

    groups: dict[str, list[float]] = {}
    for a, b, _w in rig.bones:
        if a not in pts or b not in pts:
            continue
        group = rigs._group_of(a, b)
        if not group:
            continue
        observed = math.dist(pts[a], pts[b]) / anchor
        expected = math.dist(rig.neutral[a][:3:2], rig.neutral[b][:3:2])
        if expected > 1e-6:
            groups.setdefault(group, []).append(observed / expected)

    out = {}
    for group, samples in groups.items():
        samples.sort()
        median = samples[len(samples) // 2]
        if 0.3 <= median <= 3.0 and abs(median - 1.0) > 0.12:
            out[group] = round(median, 2)
    return out


def infer_crop(annotation: Annotation) -> dict[str, Any]:
    """Which regions fall outside the frame, as words a prompt can use."""
    rig = rigs.get(annotation.rig)
    missing = set(annotation.missing())
    if not annotation.points:
        return {}

    regions = {
        "legs": ("ankle", "knee", "paw", "foot"),
        "arms": ("wrist", "elbow"),
        "head": ("nose", "head", "eye", "ear"),
    }
    absent = []
    for label, needles in regions.items():
        relevant = [j for j in rig.joints if any(n in j for n in needles)]
        if relevant and all(j in missing for j in relevant):
            absent.append(label)

    ys = [p[1] for p in annotation.points.values()]
    lowest = max(ys) if ys else 1.0
    framing = ""
    if "legs" in absent:
        framing = "waist-up composition" if lowest < 0.75 else "thigh-up composition"
    elif lowest < 0.6:
        framing = "close crop"

    return {"absent": absent, "framing": framing, "lowest_point": round(lowest, 2)}


def describe(annotation: Annotation) -> dict:
    """Everything derivable from an annotation, for the UI and the pipeline."""
    yaw, confidence = infer_view(annotation)
    return {
        **annotation.as_dict(),
        "inferred_view": yaw,
        "view_confidence": confidence,
        "proportions": infer_proportions(annotation),
        "crop": infer_crop(annotation),
    }


def gather(library) -> list[Annotation]:
    """Every annotation on the references already resolved for this run."""
    out = []
    for ref in library.identity + library.pose:
        found = load(ref.path)
        if found:
            out.append(found)
    return out
