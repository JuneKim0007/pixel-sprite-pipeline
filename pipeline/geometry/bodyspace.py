
from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

from . import rigs as _rigs
from .openpose import JOINTS
from ..shared.errors import Invalid, NotFound

# Named angles. Pokémon-style battle backs sit around 165-200: mostly rear,
# turned just enough to read as a body rather than a silhouette.
VIEWS: dict[str, float] = {
    "front": 0.0,
    "three_quarter_front": 40.0,
    "side": 90.0,
    "three_quarter_rear": 145.0,
    "rear_turned": 170.0,     # the "slightly turned back view"
    "rear": 180.0,
}

# Neutral standing pose. lateral +ve is the character's LEFT.
# Depth is small for most joints: a body is much wider than it is deep, and
# that ratio is what makes the projection read correctly at intermediate angles.
NEUTRAL: dict[str, tuple[float, float, float]] = {
    #             lateral  depth  height
    "nose":       (0.000,  0.030, 0.145),
    "neck":       (0.000,  0.000, 0.225),
    "r_shoulder": (-0.055, 0.000, 0.243),
    "r_elbow":    (-0.065, 0.002, 0.352),
    "r_wrist":    (-0.070, 0.004, 0.452),
    "l_shoulder": (0.055,  0.000, 0.243),
    "l_elbow":    (0.065,  0.002, 0.352),
    "l_wrist":    (0.070,  0.004, 0.452),
    "r_hip":      (-0.035, 0.000, 0.505),
    "r_knee":     (-0.038, 0.002, 0.655),
    "r_ankle":    (-0.040, 0.006, 0.815),
    "l_hip":      (0.035,  0.000, 0.505),
    "l_knee":     (0.038,  0.002, 0.655),
    "l_ankle":    (0.040,  0.006, 0.815),
    "r_eye":      (-0.014, 0.026, 0.136),
    "l_eye":      (0.014,  0.026, 0.136),
    "r_ear":      (-0.030, -0.004, 0.142),
    "l_ear":      (0.030,  -0.004, 0.142),
}

FACE_ONLY = ("nose", "r_eye", "l_eye")


def facing(yaw_deg: float) -> float:
    """+1 when the character faces the viewer, -1 when it faces away."""
    return math.cos(math.radians(yaw_deg))


def visible(joint: str, yaw_deg: float, rig=None) -> bool:
    """Whether a keypoint should be emitted at this angle.

    Only face joints are angle-dependent. Ears stay in at every angle: they
    carry head width, which is the main cue for head orientation once the face
    is gone.
    """
    face = set(rig.face_joints) if rig is not None else FACE_ONLY
    if joint not in face:
        return True
    f = facing(yaw_deg)
    if joint == "nose":
        return f > -0.15          # survives just past profile
    return f > 0.05               # eyes drop at profile


def frame_scale(pose: Mapping[str, Sequence[float]], fill: float) -> float:
    """The factor frame_fit would apply. Callers need it for line thickness.

    Bone thicknesses are fractions of the canvas, so enlarging a figure without
    enlarging them draws a bigger skeleton out of the same thin sticks — the
    depth capsules stop matching the limbs they represent, and the model reads
    a heavy character as a wiry one.
    """
    if fill <= 0 or not pose:
        return 1.0
    # Whichever axis is tightest decides. Scaling on height alone sent a
    # dragon to 94% of the canvas width at 88% fill, and a wingspan that
    # touches both edges is a wingspan the export crop cannot breathe around.
    # Lateral and depth are checked together because a yaw of 90 degrees swaps
    # which of them is on screen.
    spans = [
        max(p[axis] for p in pose.values()) - min(p[axis] for p in pose.values())
        for axis in (0, 1, 2)
    ]
    widest = max(spans)
    return fill / widest if widest > 1e-6 else 1.0


def frame_fit(
    pose: Mapping[str, Sequence[float]],
    *,
    fill: float,
    margin: float = 0.06,
) -> dict[str, list[float]]:
    """Scale a pose about its own centre so it fills `fill` of the frame.

    The rigs are authored at a size, and the size is small: the humanoid's
    neutral spans height 0.14 to 0.81, so a figure occupies 68% of the canvas
    and 32% is empty air. That is 32% of the generation resolution spent on
    background, and after reducing 1024 by a factor of 8 it is the difference
    between a character 87 logical pixels tall and one 112 tall — a third more
    detail for the same GPU time.

    This scales rather than re-authoring the coordinates, and it happens at
    projection time, so:

      * stored pose files keep meaning what they meant,
      * bone-length snapping still measures against the rig's own neutral,
      * and the depth map cannot disagree with the skeleton, because both go
        through this same function.

    Lateral and depth scale by the same factor as height. Scaling only height
    would stretch the character rather than enlarge it.
    """
    if fill <= 0:
        return {k: list(v) for k, v in pose.items()}

    if not pose:
        return {k: list(v) for k, v in pose.items()}
    scale = frame_scale(pose, fill)
    if abs(scale - 1.0) < 1e-9:
        return {k: list(v) for k, v in pose.items()}
    top = min(p[2] for p in pose.values())
    # Sit the figure `margin` from the top, which leaves the remainder under
    # its feet — a sprite standing on nothing looks wrong hard against the
    # bottom edge, and export crops to the subject anyway.
    return {
        joint: [p[0] * scale, p[1] * scale, margin + (p[2] - top) * scale]
        for joint, p in pose.items()
    }


def project(
    pose: Mapping[str, Sequence[float]],
    yaw_deg: float,
    *,
    centre: float = 0.5,
    depth_scale: float = 1.0,
    lateral_scale: float = 1.0,
    fill: float = 0.0,
    rig=None,
) -> list[list[float] | None]:
    """Body-space pose -> screen keypoints, in the rig's joint order.

    None marks a joint that is not visible at this angle, which is how the
    control image tells the model which way the creature faces.

    `fill` enlarges the figure to occupy that fraction of the frame; 0 leaves
    the pose at its authored size. It lives here rather than in the callers so
    the skeleton and the depth map cannot be scaled differently — they are two
    views of one pose, and a control image that disagrees with its depth map is
    worse than either alone.
    """
    rig = rig if rig is not None else _rigs.HUMANOID
    if fill:
        pose = frame_fit(pose, fill=fill)
    yaw = math.radians(yaw_deg)
    sin_y, cos_y = math.sin(yaw), math.cos(yaw)

    out: list[list[float] | None] = []
    for joint in rig.joints:
        if joint not in pose or not visible(joint, yaw_deg, rig):
            out.append(None)
            continue
        lateral, depth, height = pose[joint]
        x = centre + depth * depth_scale * sin_y - lateral * lateral_scale * cos_y
        out.append([x, height])
    return out


def project_point(
    point: Sequence[float],
    yaw_deg: float,
    *,
    centre: float = 0.5,
    depth_scale: float = 1.0,
    lateral_scale: float = 1.0,
) -> tuple[float, float]:
    """Project one arbitrary body-space point, not just a named joint.

    Soft-body nodes hang off the skeleton at their own offsets, so they need
    the same projection as joints without being part of the 18-point layout.
    """
    yaw = math.radians(yaw_deg)
    lateral, depth, height = point
    x = centre + depth * depth_scale * math.sin(yaw) - lateral * lateral_scale * math.cos(yaw)
    return x, height


def view_depth(
    pose: Mapping[str, Sequence[float]], yaw_deg: float
) -> dict[str, float]:
    """Per-joint distance toward the viewer, for building a depth map.

    The component of each joint along the view axis, which is the information a
    2D skeleton throws away. Two viewing angles can project to nearly identical
    keypoints — 145 degrees and 180 degrees differ by only ~18% in horizontal
    spread — while their depth profiles are obviously different: at 145 the
    shoulders sit at different distances, at 180 they are level.

    Available for free because poses are authored in body space; nothing is
    estimated.
    """
    yaw = math.radians(yaw_deg)
    sin_y, cos_y = math.sin(yaw), math.cos(yaw)
    return {
        joint: value[1] * cos_y + value[0] * sin_y
        for joint, value in pose.items()
        if len(value) == 3
    }


def blend(
    a: Mapping[str, Sequence[float]], b: Mapping[str, Sequence[float]], t: float
) -> dict[str, tuple[float, float, float]]:
    """Linear interpolation between two body-space poses.

    Tweening in body space rather than screen space keeps in-betweens correct
    at every viewing angle, because the interpolation happens before projection.
    """
    keys = set(a) | set(b)
    out: dict[str, tuple[float, float, float]] = {}
    for k in keys:
        pa, pb = a.get(k), b.get(k)
        if pa is None or pb is None:
            continue
        out[k] = tuple(pa[i] + (pb[i] - pa[i]) * t for i in range(3))  # type: ignore[misc]
    return out


BONES: tuple[tuple[str, str], ...] = (
    ("neck", "nose"),
    ("neck", "r_shoulder"), ("r_shoulder", "r_elbow"), ("r_elbow", "r_wrist"),
    ("neck", "l_shoulder"), ("l_shoulder", "l_elbow"), ("l_elbow", "l_wrist"),
    ("neck", "r_hip"), ("r_hip", "r_knee"), ("r_knee", "r_ankle"),
    ("neck", "l_hip"), ("l_hip", "l_knee"), ("l_knee", "l_ankle"),
)


def bone_length(pose: Mapping[str, Sequence[float]], a: str, b: str) -> float | None:
    pa, pb = pose.get(a), pose.get(b)
    if pa is None or pb is None:
        return None
    return math.dist(pa, pb)


# Parent -> children, rooted at the neck. Used to walk a skeleton outward so a
# joint is always corrected after the joint it hangs from.
SKELETON_TREE: dict[str, tuple[str, ...]] = {
    "neck": ("nose", "r_shoulder", "l_shoulder", "r_hip", "l_hip"),
    "r_shoulder": ("r_elbow",),
    "r_elbow": ("r_wrist",),
    "l_shoulder": ("l_elbow",),
    "l_elbow": ("l_wrist",),
    "r_hip": ("r_knee",),
    "r_knee": ("r_ankle",),
    "l_hip": ("l_knee",),
    "l_knee": ("l_ankle",),
    "nose": ("r_eye", "l_eye", "r_ear", "l_ear"),
}


def snap_to_anatomy(pose: Mapping[str, Sequence[float]], rig=None) -> dict[str, list[float]]:
    """Force exact bone lengths while keeping each limb's direction.

    This is what makes a language model a usable pose generator. An LLM is good
    at the qualitative part — "the sword arm points forward and down" — and bad
    at the quantitative part — "the wrist is 0.148 units from the elbow".
    Rejecting it for the second failing wastes a capability it does have.

    So we keep the direction it chose for every bone and rescale that bone to
    its exact neutral length, walking outward from the neck. Intent survives,
    anatomy becomes exact by construction, and the generator only has to be
    right about something it is actually good at.
    """
    rig = rig if rig is not None else _rigs.HUMANOID
    neutral, tree = rig.neutral, rig.tree

    out = {k: list(v) for k, v in neutral.items()}
    for k, v in pose.items():
        if k in out and len(v) == 3:
            out[k] = [float(x) for x in v]

    queue = [rig.root]
    while queue:
        parent = queue.pop(0)
        for child in tree.get(parent, ()):
            want = bone_length(neutral, parent, child)
            if want is None:
                continue
            px, py, pz = out[parent]
            cx, cy, cz = out[child]
            dx, dy, dz = cx - px, cy - py, cz - pz
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if dist < 1e-6:
                # Degenerate: fall back to the neutral offset direction.
                nx, ny, nz = neutral[child]
                bx, by, bz = neutral[parent]
                dx, dy, dz = nx - bx, ny - by, nz - bz
                dist = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            scale = want / dist
            out[child] = [px + dx * scale, py + dy * scale, pz + dz * scale]
            queue.append(child)

    return out


def validate_pose(
    pose: Mapping[str, Sequence[float]], tolerance: float = 0.3, rig=None
) -> list[str]:
    """Return a list of problems; empty means the pose is anatomically sane.

    This is the check that makes generated poses usable. Bone lengths are
    invariant in body space — a real skeleton's forearm does not change length
    when it moves — so any generator that stretches one has produced something
    wrong, and we can say so precisely instead of discovering it in a sprite.

    Cheap to run, which means a generator can be retried until it passes.
    """
    rig = rig if rig is not None else _rigs.HUMANOID
    problems: list[str] = []

    structural = [rig.root] + list(rig.tree.get(rig.root, ())[:2])
    missing = [j for j in structural if j not in pose]
    if missing:
        problems.append(f"missing structural joint(s): {missing}")
        return problems

    for joint, value in pose.items():
        if joint not in rig.joints:
            problems.append(f"unknown joint '{joint}'")
            continue
        if len(value) != 3:
            problems.append(f"{joint}: expected 3 numbers, got {len(value)}")
            continue
        lateral, depth, height = value
        if not -0.5 <= lateral <= 0.5:
            problems.append(f"{joint}: lateral {lateral:.3f} out of range [-0.5, 0.5]")
        if not -0.5 <= depth <= 0.5:
            problems.append(f"{joint}: depth {depth:.3f} out of range [-0.5, 0.5]")
        if not 0.0 <= height <= 1.0:
            problems.append(f"{joint}: height {height:.3f} out of range [0, 1]")

    for a, b, _ in rig.bones:
        got, want = bone_length(pose, a, b), bone_length(rig.neutral, a, b)
        if got is None or want is None or want == 0:
            continue
        ratio = got / want
        if not (1 - tolerance) <= ratio <= (1 + tolerance):
            problems.append(
                f"bone {a}->{b} is {ratio:.2f}x its neutral length "
                f"(allowed {1 - tolerance:.2f}-{1 + tolerance:.2f})"
            )

    return problems


def resolve_view(view: str | float) -> float:
    """Accept either a named view or a raw angle in degrees."""
    if isinstance(view, (int, float)):
        return float(view)
    key = str(view).strip().lower()
    if key in VIEWS:
        return VIEWS[key]
    try:
        return float(key)
    except ValueError:
        raise NotFound("view", str(view), available=list(VIEWS),
                       hint="a number of degrees, or one of: "
                            + ", ".join(f"{k} ({v:g}°)"
                                        for k, v in VIEWS.items())) from None


def pose_from(base: Mapping[str, Sequence[float]], rig=None, **overrides) -> dict:
    known = set(rig.joints) if rig is not None else set(JOINTS)
    unknown = set(overrides) - known
    if unknown:
        raise Invalid(f"unknown joint(s): {sorted(unknown)}", field="overrides",
                      hint=f"this rig has: {', '.join(sorted(known))}")
    return {**base, **overrides}


def all_views() -> Iterable[tuple[str, float]]:
    return VIEWS.items()
