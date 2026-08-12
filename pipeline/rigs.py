"""Rig templates: what a skeleton *is*, per creature type.

Everything used to assume one humanoid — an 18-joint COCO layout with named
arms and legs, hardcoded in bodyspace. That is the reason a dragon or a snake
could not be posed: not a missing feature, a missing abstraction.

A Rig carries the whole description of a body plan, so the pose, depth and
frame stages read it instead of assuming.

The important asymmetry, and the reason non-humanoid support is cheap:

  the SKELETON channel is topology-locked   OpenPose ControlNet was trained on
                                            exactly 18 human joints; feed it a
                                            centipede and it reads the segments
                                            as misplaced limbs

  the DEPTH channel is not                  we render depth ourselves, from
                                            body-space coordinates, so any
                                            topology at all produces a valid
                                            depth map

So a humanoid gets openpose + depth, and everything else leans on depth plus
scribble — which is the most forgiving conditioning type and does not care what
the drawing depicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
from .shared.registry import Decorated, Registry

# The canonical OpenPose colour wheel. ControlNet was trained against these
# exact hues, so for the humanoid rig they are protocol, not decoration.
# Other rigs reuse the wheel purely so limbs stay visually distinguishable.
COLORS: tuple[tuple[int, int, int], ...] = (
    (255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0),
    (85, 255, 0), (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255),
    (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255),
    (255, 0, 255), (255, 0, 170), (255, 0, 85),
)


@dataclass(frozen=True)
class Rig:
    """A body plan: which joints exist, how they connect, and how to condition."""

    name: str
    label: str
    joints: tuple[str, ...]
    # parent -> children, rooted at `root`. Used for bone-length snapping and
    # for painting depth capsules far-to-near.
    tree: dict[str, tuple[str, ...]]
    root: str
    # (lateral, depth, height) per joint. lateral +ve is the creature's LEFT,
    # depth +ve is in front of it, height 0 is the top and 1 the ground.
    neutral: dict[str, tuple[float, float, float]]
    # Bones to draw, as joint-name pairs, plus their depth-map thickness as a
    # fraction of the canvas.
    bones: tuple[tuple[str, str, float], ...]
    # Conditioning. `skeleton_control` is the ControlNet union type for the
    # rendered stick figure; None means do not send one at all.
    skeleton_control: str | None
    depth_control: str = "depth"
    # Joints that vanish once the creature faces away. Their presence or
    # absence is how the control image communicates facing.
    face_joints: tuple[str, ...] = ()
    head_joint: str = ""
    head_radius: float = 0.062
    prompt_hint: str = ""
    note: str = ""

    def index(self, joint: str) -> int:
        return self.joints.index(joint)

    @property
    def limb_pairs(self) -> tuple[tuple[int, int], ...]:
        return tuple((self.index(a), self.index(b)) for a, b, _ in self.bones)

    @property
    def thickness(self) -> dict[tuple[str, str], float]:
        return {(a, b): t for a, b, t in self.bones}

    def color(self, i: int) -> tuple[int, int, int]:
        return COLORS[i % len(COLORS)]

    def describe(self) -> str:
        channels = [c for c in (self.skeleton_control, self.depth_control) if c]
        return f"{self.label}: {len(self.joints)} joints, control via {' + '.join(channels)}"


# --------------------------------------------------------------- humanoid

# Unchanged from the original layout — the index order IS the OpenPose
# protocol and must not be reordered.
_HUMANOID_JOINTS = (
    "nose", "neck",
    "r_shoulder", "r_elbow", "r_wrist",
    "l_shoulder", "l_elbow", "l_wrist",
    "r_hip", "r_knee", "r_ankle",
    "l_hip", "l_knee", "l_ankle",
    "r_eye", "l_eye", "r_ear", "l_ear",
)

HUMANOID = Rig(
    name="humanoid",
    label="Humanoid",
    joints=_HUMANOID_JOINTS,
    root="neck",
    tree={
        "neck": ("nose", "r_shoulder", "l_shoulder", "r_hip", "l_hip"),
        "r_shoulder": ("r_elbow",), "r_elbow": ("r_wrist",),
        "l_shoulder": ("l_elbow",), "l_elbow": ("l_wrist",),
        "r_hip": ("r_knee",), "r_knee": ("r_ankle",),
        "l_hip": ("l_knee",), "l_knee": ("l_ankle",),
        "nose": ("r_eye", "l_eye", "r_ear", "l_ear"),
    },
    neutral={
        "nose":       (0.000, 0.030, 0.145),
        "neck":       (0.000, 0.000, 0.225),
        "r_shoulder": (-0.055, 0.000, 0.243),
        "r_elbow":    (-0.065, 0.002, 0.352),
        "r_wrist":    (-0.070, 0.004, 0.452),
        "l_shoulder": (0.055, 0.000, 0.243),
        "l_elbow":    (0.065, 0.002, 0.352),
        "l_wrist":    (0.070, 0.004, 0.452),
        "r_hip":      (-0.035, 0.000, 0.505),
        "r_knee":     (-0.038, 0.002, 0.655),
        "r_ankle":    (-0.040, 0.006, 0.815),
        "l_hip":      (0.035, 0.000, 0.505),
        "l_knee":     (0.038, 0.002, 0.655),
        "l_ankle":    (0.040, 0.006, 0.815),
        "r_eye":      (-0.014, 0.026, 0.136),
        "l_eye":      (0.014, 0.026, 0.136),
        "r_ear":      (-0.030, -0.004, 0.142),
        "l_ear":      (0.030, -0.004, 0.142),
    },
    bones=(
        ("neck", "r_hip", 0.115), ("neck", "l_hip", 0.115),
        ("neck", "r_shoulder", 0.075), ("neck", "l_shoulder", 0.075),
        ("r_shoulder", "r_elbow", 0.045), ("l_shoulder", "l_elbow", 0.045),
        ("r_elbow", "r_wrist", 0.036), ("l_elbow", "l_wrist", 0.036),
        ("r_hip", "r_knee", 0.055), ("l_hip", "l_knee", 0.055),
        ("r_knee", "r_ankle", 0.042), ("l_knee", "l_ankle", 0.042),
        ("neck", "nose", 0.070),
    ),
    skeleton_control="openpose",
    face_joints=("nose", "r_eye", "l_eye"),
    head_joint="nose",
    prompt_hint="",
    note="The only rig with a matching ControlNet. Uses openpose + depth.",
)

# --------------------------------------------------------------- quadruped

# Body runs along the depth axis rather than standing upright, so height stays
# roughly constant across the spine and the legs drop from it.
QUADRUPED = Rig(
    name="quadruped",
    label="Quadruped",
    joints=(
        "head", "neck", "chest", "spine", "hips",
        "tail_base", "tail_mid", "tail_tip",
        "l_front_shoulder", "l_front_knee", "l_front_paw",
        "r_front_shoulder", "r_front_knee", "r_front_paw",
        "l_back_hip", "l_back_knee", "l_back_paw",
        "r_back_hip", "r_back_knee", "r_back_paw",
    ),
    root="chest",
    tree={
        "chest": ("neck", "spine", "l_front_shoulder", "r_front_shoulder"),
        "neck": ("head",),
        "spine": ("hips",),
        "hips": ("tail_base", "l_back_hip", "r_back_hip"),
        "tail_base": ("tail_mid",), "tail_mid": ("tail_tip",),
        "l_front_shoulder": ("l_front_knee",), "l_front_knee": ("l_front_paw",),
        "r_front_shoulder": ("r_front_knee",), "r_front_knee": ("r_front_paw",),
        "l_back_hip": ("l_back_knee",), "l_back_knee": ("l_back_paw",),
        "r_back_hip": ("r_back_knee",), "r_back_knee": ("r_back_paw",),
    },
    neutral={
        "head":             (0.000, 0.330, 0.330),
        "neck":             (0.000, 0.225, 0.395),
        "chest":            (0.000, 0.120, 0.450),
        "spine":            (0.000, -0.010, 0.445),
        "hips":             (0.000, -0.145, 0.450),
        "tail_base":        (0.000, -0.205, 0.440),
        "tail_mid":         (0.000, -0.285, 0.405),
        "tail_tip":         (0.000, -0.350, 0.350),
        "l_front_shoulder": (0.050, 0.115, 0.480),
        "l_front_knee":     (0.052, 0.128, 0.660),
        "l_front_paw":      (0.054, 0.140, 0.850),
        "r_front_shoulder": (-0.050, 0.115, 0.480),
        "r_front_knee":     (-0.052, 0.128, 0.660),
        "r_front_paw":      (-0.054, 0.140, 0.850),
        "l_back_hip":       (0.050, -0.145, 0.480),
        "l_back_knee":      (0.052, -0.170, 0.660),
        "l_back_paw":       (0.054, -0.145, 0.850),
        "r_back_hip":       (-0.050, -0.145, 0.480),
        "r_back_knee":      (-0.052, -0.170, 0.660),
        "r_back_paw":       (-0.054, -0.145, 0.850),
    },
    bones=(
        ("chest", "spine", 0.130), ("spine", "hips", 0.130),
        ("chest", "neck", 0.085), ("neck", "head", 0.075),
        ("hips", "tail_base", 0.055), ("tail_base", "tail_mid", 0.040),
        ("tail_mid", "tail_tip", 0.026),
        ("chest", "l_front_shoulder", 0.060), ("chest", "r_front_shoulder", 0.060),
        ("l_front_shoulder", "l_front_knee", 0.044),
        ("r_front_shoulder", "r_front_knee", 0.044),
        ("l_front_knee", "l_front_paw", 0.034),
        ("r_front_knee", "r_front_paw", 0.034),
        ("hips", "l_back_hip", 0.062), ("hips", "r_back_hip", 0.062),
        ("l_back_hip", "l_back_knee", 0.048), ("r_back_hip", "r_back_knee", 0.048),
        ("l_back_knee", "l_back_paw", 0.036), ("r_back_knee", "r_back_paw", 0.036),
    ),
    # No ControlNet understands a quadruped skeleton, but scribble does not
    # need to: it treats the drawing as a rough sketch to follow.
    skeleton_control="hed/pidi/scribble/ted",
    face_joints=("head",),
    head_joint="head",
    head_radius=0.075,
    prompt_hint="four-legged creature, side view",
    note="No quadruped OpenPose model, so the skeleton is sent as a scribble.",
)

# ----------------------------------------------------------------- serpent

def _serpent(segments: int = 12, *, name: str = "serpent",
             label: str = "Serpent / segmented") -> Rig:
    """A chain body: snakes, centipedes, worms, dragons in flight.

    Generated rather than written out because the only thing that varies is
    how many segments there are, and hand-listing twelve near-identical joints
    invites typos.
    """
    joints = ["head"] + [f"seg_{i:02d}" for i in range(segments)]
    tree: dict[str, tuple[str, ...]] = {"head": ("seg_00",)}
    for i in range(segments - 1):
        tree[f"seg_{i:02d}"] = (f"seg_{i + 1:02d}",)

    neutral: dict[str, tuple[float, float, float]] = {
        "head": (0.0, 0.34, 0.42),
    }
    bones: list[tuple[str, str, float]] = [("head", "seg_00", 0.070)]
    import math

    for i in range(segments):
        t = i / max(segments - 1, 1)
        # A gentle S so the neutral pose does not read as a straight stick.
        neutral[f"seg_{i:02d}"] = (
            0.055 * math.sin(t * math.pi * 1.6),
            0.30 - t * 0.62,
            0.44 + 0.05 * math.sin(t * math.pi * 1.2),
        )
        if i:
            width = 0.062 * (1.0 - 0.55 * t)   # tapers toward the tail
            bones.append((f"seg_{i - 1:02d}", f"seg_{i:02d}", width))

    return Rig(
        name=name,
        label=label,
        joints=tuple(joints),
        root="head",
        tree=tree,
        neutral=neutral,
        bones=tuple(bones),
        skeleton_control="hed/pidi/scribble/ted",
        face_joints=("head",),
        head_joint="head",
        head_radius=0.055,
        prompt_hint="long segmented body, sinuous",
        note="Chain topology. OpenPose would read the segments as broken limbs.",
    )


SERPENT = _serpent()

# -------------------------------------------------------------------- blob

# Almost no skeleton on purpose: a slime deforms rather than articulates, so
# the interesting motion belongs to soft-body nodes, not joints.
BLOB = Rig(
    name="blob",
    label="Blob / amorphous",
    joints=("core", "top", "base", "l_side", "r_side"),
    root="core",
    tree={"core": ("top", "base", "l_side", "r_side")},
    neutral={
        "core":   (0.000, 0.000, 0.620),
        "top":    (0.000, 0.000, 0.420),
        "base":   (0.000, 0.000, 0.840),
        "l_side": (0.150, 0.000, 0.660),
        "r_side": (-0.150, 0.000, 0.660),
    },
    bones=(
        ("core", "top", 0.240), ("core", "base", 0.290),
        ("core", "l_side", 0.230), ("core", "r_side", 0.230),
    ),
    # A stick figure would actively mislead here; the silhouette is the whole
    # subject, so only the depth blob is sent.
    skeleton_control=None,
    face_joints=(),
    head_joint="top",
    head_radius=0.10,
    prompt_hint="gelatinous, soft rounded silhouette",
    note="Depth only. Add soft-body nodes for wobble — that is where the life is.",
)


# ------------------------------------------------------- parametric plans

# Body plans vary along a few axes — how many legs, how many arms, tail, wings,
# segment count — so the library is generated from those axes rather than
# hand-written per creature. Fifty bespoke rigs would be fifty places for a
# typo; six generators cover the same ground and stay consistent.


def _limb_chain(
    prefix: str, base: tuple[float, float, float], reach: float,
    *, segments: int = 2, spread: float = 0.0, widths: tuple[float, ...] = (0.045, 0.034),
) -> tuple[dict, dict, list]:
    """A jointed limb hanging from `base`, as (neutral, tree, bones)."""
    neutral, tree, bones = {}, {}, []
    prev, prev_pos = None, base
    for i in range(segments):
        t = (i + 1) / segments
        name = f"{prefix}_{i}"
        neutral[name] = (
            base[0] + spread * t,
            base[1] + (prev_pos[1] - base[1]) * 0.2,
            base[2] + reach * t,
        )
        if prev:
            tree[prev] = (name,)
            bones.append((prev, name, widths[min(i, len(widths) - 1)]))
        prev, prev_pos = name, neutral[name]
    return neutral, tree, bones


def humanoid(arms: int = 2, *, name: str = "", label: str = "", tail: bool = False) -> Rig:
    """The COCO humanoid, optionally with extra arm pairs or a tail.

    Two arms returns HUMANOID untouched, because that exact joint list and
    order is the OpenPose protocol. Any variation drops to scribble control —
    a four-armed figure sent to an OpenPose ControlNet reads as a mangled
    two-armed one.
    """
    if arms == 2 and not tail:
        return HUMANOID

    joints = list(_HUMANOID_JOINTS)
    tree = {k: tuple(v) for k, v in HUMANOID.tree.items()}
    neutral = dict(HUMANOID.neutral)
    bones = list(HUMANOID.bones)

    pairs = (arms - 2) // 2
    for p in range(pairs):
        drop = 0.055 * (p + 1)          # lower pairs sit further down the torso
        for side, sign in (("l", 1), ("r", -1)):
            sh = f"{side}_shoulder_{p + 2}"
            el = f"{side}_elbow_{p + 2}"
            wr = f"{side}_wrist_{p + 2}"
            neutral[sh] = (sign * 0.052, -0.010, 0.243 + drop)
            neutral[el] = (sign * 0.070, 0.004, 0.352 + drop)
            neutral[wr] = (sign * 0.078, 0.010, 0.452 + drop)
            joints += [sh, el, wr]
            tree["neck"] = tree["neck"] + (sh,)
            tree[sh], tree[el] = (el,), (wr,)
            bones += [("neck", sh, 0.070), (sh, el, 0.042), (el, wr, 0.032)]

    if tail:
        prev = "neck"
        for i, (depth, height, width) in enumerate(
            ((-0.10, 0.560, 0.048), (-0.19, 0.640, 0.036), (-0.26, 0.720, 0.024))
        ):
            j = f"tail_{i}"
            neutral[j] = (0.0, depth, height)
            joints.append(j)
            tree[prev] = tree.get(prev, ()) + (j,)
            bones.append((prev, j, width))
            prev = j

    return Rig(
        name=name or f"humanoid_{arms}arm" + ("_tailed" if tail else ""),
        label=label or f"Humanoid, {arms} arms" + (" + tail" if tail else ""),
        joints=tuple(joints), root="neck", tree=tree, neutral=neutral,
        bones=tuple(bones),
        skeleton_control="hed/pidi/scribble/ted",
        face_joints=("nose", "r_eye", "l_eye"),
        head_joint="nose",
        prompt_hint=(f"{arms} arms, " if arms > 2 else "") + ("long tail" if tail else ""),
        note="Non-standard limb count, so the skeleton goes out as a scribble.",
    )


def multileg(
    legs: int = 6, *, name: str = "", label: str = "", body_len: float = 0.30,
    label_hint: str = "", tail: bool = False, wings: int = 0,
) -> Rig:
    """Insects, spiders, and anything else whose legs come in ranks.

    Legs are distributed along the body axis so a hexapod reads as an insect
    and an octopod as a spider, rather than as a centipede with a few legs.
    """
    import math

    ranks = max(legs // 2, 1)
    joints = ["head", "thorax", "abdomen"]
    neutral = {
        "head":    (0.0, body_len * 0.62, 0.470),
        "thorax":  (0.0, body_len * 0.16, 0.500),
        "abdomen": (0.0, -body_len * 0.50, 0.505),
    }
    tree: dict[str, tuple[str, ...]] = {"thorax": ("head", "abdomen")}
    bones = [("thorax", "head", 0.090), ("thorax", "abdomen", 0.130)]

    for rank in range(ranks):
        t = rank / max(ranks - 1, 1)
        anchor_depth = body_len * (0.26 - 0.52 * t)
        for side, sign in (("l", 1), ("r", -1)):
            hip = f"{side}_leg{rank}_hip"
            knee = f"{side}_leg{rank}_knee"
            foot = f"{side}_leg{rank}_foot"
            # Legs bow outward then down, which is what makes an arthropod
            # silhouette recognisable at sprite size.
            neutral[hip] = (sign * 0.045, anchor_depth, 0.505)
            neutral[knee] = (sign * 0.135, anchor_depth + 0.02, 0.590)
            neutral[foot] = (sign * 0.150, anchor_depth + 0.03, 0.840)
            joints += [hip, knee, foot]
            tree["thorax"] = tree.get("thorax", ()) + (hip,)
            tree[hip], tree[knee] = (knee,), (foot,)
            bones += [("thorax", hip, 0.040), (hip, knee, 0.030), (knee, foot, 0.022)]

    if tail:
        prev = "abdomen"
        for i in range(3):
            j = f"tail_{i}"
            neutral[j] = (0.0, -body_len * (0.62 + 0.22 * i), 0.480 - 0.045 * i)
            joints.append(j)
            tree[prev] = tree.get(prev, ()) + (j,)
            bones.append((prev, j, 0.046 - 0.010 * i))
            prev = j

    for w in range(wings):
        side, sign = ("l", 1) if w % 2 == 0 else ("r", -1)
        idx = w // 2
        root = f"{side}_wing{idx}_root"
        tip = f"{side}_wing{idx}_tip"
        neutral[root] = (sign * 0.040, 0.02 - 0.10 * idx, 0.455)
        neutral[tip] = (sign * 0.230, -0.10 - 0.12 * idx, 0.330)
        joints += [root, tip]
        tree["thorax"] = tree.get("thorax", ()) + (root,)
        tree[root] = (tip,)
        bones += [("thorax", root, 0.040), (root, tip, 0.070)]

    return Rig(
        name=name or f"arthropod_{legs}leg",
        label=label or f"{legs}-legged arthropod",
        joints=tuple(joints), root="thorax", tree=tree, neutral=neutral,
        bones=tuple(bones),
        skeleton_control="hed/pidi/scribble/ted",
        face_joints=("head",), head_joint="head", head_radius=0.070,
        prompt_hint=label_hint or f"{legs} legs, chitinous",
        note="Radial leg ranks. OpenPose has no concept of this.",
    )


def winged_quadruped(*, name: str = "dragon", label: str = "Dragon", wings: bool = True) -> Rig:
    """A quadruped with a wing pair — dragons, griffins, wyverns."""
    base = QUADRUPED
    joints = list(base.joints)
    tree = {k: tuple(v) for k, v in base.tree.items()}
    neutral = dict(base.neutral)
    bones = list(base.bones)

    if wings:
        for side, sign in (("l", 1), ("r", -1)):
            root = f"{side}_wing_root"
            mid = f"{side}_wing_mid"
            tip = f"{side}_wing_tip"
            neutral[root] = (sign * 0.055, 0.060, 0.410)
            neutral[mid] = (sign * 0.190, -0.030, 0.300)
            neutral[tip] = (sign * 0.330, -0.130, 0.235)
            joints += [root, mid, tip]
            tree["chest"] = tree["chest"] + (root,)
            tree[root], tree[mid] = (mid,), (tip,)
            bones += [("chest", root, 0.060), (root, mid, 0.048), (mid, tip, 0.034)]

    return Rig(
        name=name, label=label,
        joints=tuple(joints), root="chest", tree=tree, neutral=neutral,
        bones=tuple(bones),
        skeleton_control="hed/pidi/scribble/ted",
        face_joints=("head",), head_joint="head", head_radius=0.078,
        prompt_hint="winged beast, membranous wings, long tail",
        note="Quadruped plus a wing pair.",
    )


def wyvern(*, name: str = "wyvern", label: str = "Wyvern (wings, no forelegs)") -> Rig:
    """A dragon whose forelimbs ARE the wings — no separate front legs.

    Worth its own generator rather than a flag: dropping the forelegs changes
    the silhouette enough that reusing the quadruped rig would produce a
    six-limbed dragon by accident.
    """
    base = winged_quadruped(name=name, label=label)
    drop = {j for j in base.joints if "front" in j}
    joints = tuple(j for j in base.joints if j not in drop)
    tree = {
        k: tuple(c for c in v if c not in drop)
        for k, v in base.tree.items() if k not in drop
    }
    return Rig(
        name=name, label=label, joints=joints, root="chest", tree=tree,
        neutral={k: v for k, v in base.neutral.items() if k not in drop},
        bones=tuple(b for b in base.bones if b[0] not in drop and b[1] not in drop),
        skeleton_control="hed/pidi/scribble/ted",
        face_joints=("head",), head_joint="head", head_radius=0.078,
        prompt_hint="wyvern, wings as forelimbs, two hind legs, long tail",
        note="Wings replace the forelegs.",
    )


def tentacled(arms: int = 8, *, name: str = "", label: str = "", segments: int = 3) -> Rig:
    """A central mass with radiating flexible arms — octopus, squid, horror."""
    import math

    joints = ["core", "head"]
    neutral = {"core": (0.0, 0.0, 0.430), "head": (0.0, 0.030, 0.330)}
    tree: dict[str, tuple[str, ...]] = {"core": ("head",)}
    bones = [("core", "head", 0.150)]

    for a in range(arms):
        angle = (a / arms) * math.tau
        prev = "core"
        for s in range(segments):
            t = (s + 1) / segments
            j = f"arm{a}_{s}"
            neutral[j] = (
                math.cos(angle) * 0.16 * t,
                math.sin(angle) * 0.10 * t,
                0.470 + 0.34 * t,
            )
            joints.append(j)
            tree[prev] = tree.get(prev, ()) + (j,)
            bones.append((prev, j, 0.046 * (1 - 0.5 * t)))
            prev = j

    return Rig(
        name=name or f"tentacled_{arms}",
        label=label or f"Tentacled, {arms} arms",
        joints=tuple(joints), root="core", tree=tree, neutral=neutral,
        bones=tuple(bones),
        skeleton_control="hed/pidi/scribble/ted",
        face_joints=("head",), head_joint="head", head_radius=0.090,
        prompt_hint="writhing tentacles, radial symmetry",
        note="Radial chains. Pair with soft-body nodes for the writhe.",
    )


def avian(*, name: str = "avian", label: str = "Bird / harpy") -> Rig:
    """Two legs plus a wing pair, upright — birds, harpies, angels."""
    joints = ["head", "neck", "chest", "hips", "tail",
              "l_wing_root", "l_wing_mid", "l_wing_tip",
              "r_wing_root", "r_wing_mid", "r_wing_tip",
              "l_hip", "l_knee", "l_foot", "r_hip", "r_knee", "r_foot"]
    neutral = {
        "head": (0.0, 0.055, 0.140), "neck": (0.0, 0.020, 0.235),
        "chest": (0.0, 0.0, 0.330), "hips": (0.0, -0.030, 0.500),
        "tail": (0.0, -0.150, 0.560),
        "l_wing_root": (0.060, -0.010, 0.320), "l_wing_mid": (0.220, -0.040, 0.260),
        "l_wing_tip": (0.360, -0.090, 0.330),
        "r_wing_root": (-0.060, -0.010, 0.320), "r_wing_mid": (-0.220, -0.040, 0.260),
        "r_wing_tip": (-0.360, -0.090, 0.330),
        "l_hip": (0.038, 0.0, 0.520), "l_knee": (0.040, 0.020, 0.660),
        "l_foot": (0.042, 0.055, 0.850),
        "r_hip": (-0.038, 0.0, 0.520), "r_knee": (-0.040, 0.020, 0.660),
        "r_foot": (-0.042, 0.055, 0.850),
    }
    tree = {
        "chest": ("neck", "hips", "l_wing_root", "r_wing_root"),
        "neck": ("head",), "hips": ("tail", "l_hip", "r_hip"),
        "l_wing_root": ("l_wing_mid",), "l_wing_mid": ("l_wing_tip",),
        "r_wing_root": ("r_wing_mid",), "r_wing_mid": ("r_wing_tip",),
        "l_hip": ("l_knee",), "l_knee": ("l_foot",),
        "r_hip": ("r_knee",), "r_knee": ("r_foot",),
    }
    bones = (
        ("chest", "neck", 0.080), ("neck", "head", 0.070),
        ("chest", "hips", 0.130), ("hips", "tail", 0.070),
        ("chest", "l_wing_root", 0.060), ("l_wing_root", "l_wing_mid", 0.055),
        ("l_wing_mid", "l_wing_tip", 0.040),
        ("chest", "r_wing_root", 0.060), ("r_wing_root", "r_wing_mid", 0.055),
        ("r_wing_mid", "r_wing_tip", 0.040),
        ("hips", "l_hip", 0.050), ("l_hip", "l_knee", 0.040), ("l_knee", "l_foot", 0.030),
        ("hips", "r_hip", 0.050), ("r_hip", "r_knee", 0.040), ("r_knee", "r_foot", 0.030),
    )
    return Rig(
        name=name, label=label, joints=tuple(joints), root="chest", tree=tree,
        neutral=neutral, bones=bones,
        skeleton_control="hed/pidi/scribble/ted",
        face_joints=("head",), head_joint="head", head_radius=0.068,
        prompt_hint="feathered wings, taloned feet",
        note="Upright biped with a wing pair.",
    )


# The shipped library. Every entry is one call into a generator above, so
# adding a creature is a line, not a file.
_GENERATED: tuple[Rig, ...] = (
    humanoid(4, name="humanoid_4arm", label="Humanoid, 4 arms"),
    humanoid(6, name="humanoid_6arm", label="Humanoid, 6 arms"),
    humanoid(2, tail=True, name="humanoid_tailed", label="Humanoid + tail"),
    winged_quadruped(name="dragon", label="Dragon (winged quadruped)"),
    wyvern(),
    multileg(6, name="insect", label="Insect (6 legs)", label_hint="six legs, antennae, chitin"),
    multileg(8, name="spider", label="Spider (8 legs)", label_hint="eight legs, arachnid"),
    multileg(6, name="scorpion", label="Scorpion (6 legs + tail)", tail=True,
             label_hint="segmented tail with stinger, pincers"),
    multileg(6, name="beetle_winged", label="Winged insect", wings=2,
             label_hint="iridescent wings, six legs"),
    tentacled(8, name="octopoid", label="Octopoid (8 tentacles)"),
    tentacled(4, name="tentacled_4", label="Tentacled, 4 arms"),
    avian(),
    _serpent(20, name="centipede", label="Centipede / long segmented"),
)

# ------------------------------------------------------------ proportions

# Which bones a named proportion controls. Matched against joint names, so one
# table serves every rig: a dragon's neck and a humanoid's neck are both "neck"
# because both connect a head to a torso.
# How much of a length change carries into limb thickness. 1.0 would make a
# tall character a wide one; 0.0 makes an elongated limb spindly. 0.35 turns a
# 1.75x lengthening into a 1.21x widening.
THICKNESS_EXPONENT = 0.35

PROPORTION_GROUPS: dict[str, tuple[str, ...]] = {
    "head":      ("nose", "head", "eye", "ear"),
    "neck":      ("neck",),
    "torso":     ("hip", "spine", "chest", "abdomen", "thorax", "core"),
    "arms":      ("shoulder", "elbow", "wrist"),
    "legs":      ("knee", "ankle", "paw", "foot", "leg"),
    "tail":      ("tail",),
    "wings":     ("wing",),
    "tentacles": ("arm0", "arm1", "arm2", "arm3", "arm4", "arm5", "arm6", "arm7"),
    "segments":  ("seg_",),
}


# Joints that belong to the head, for telling a neck bone from a torso bone.
_HEADWARD = ("nose", "head", "eye", "ear", "skull", "jaw", "horn")


def _group_of(parent: str, child: str) -> str | None:
    """Classify a bone, not just its far end.

    Direction matters at the neck, and getting it wrong silently disabled a
    knob. A humanoid's only true neck bone runs neck->nose; the bone running
    neck->hip is the TORSO. The first version returned "neck" whenever either
    end was named neck, so neck->hip was filed as neck — and since no rig has
    a separate torso bone, `proportions.torso` scaled nothing at all on the
    humanoid. It was set in a shipped style sheet and measured as a no-op:
    5.21 heads with it, 5.21 heads without.

    So the neck case is direction-aware. Everything else classifies by the
    child, which is the end that names the limb.
    """
    if "neck" in parent:
        return "neck" if any(n in child for n in _HEADWARD) else _by_name(child)
    if "neck" in child:
        return "neck"
    return _by_name(child)


def _by_name(joint: str) -> str | None:
    for group, needles in PROPORTION_GROUPS.items():
        if any(n in joint for n in needles):
            return group
    return None


def scale(rig: Rig, proportions: dict[str, float] | None) -> Rig:
    """A copy of `rig` with named bone groups lengthened or shortened.

    Rigs ship with one set of proportions, which is the right default and the
    wrong answer for a long-necked horror or a stubby-legged brawler. Rather
    than adding a rig per silhouette, a factor per group stretches the bones
    that group names and carries everything below them along, so the skeleton
    stays connected:

        proportions: {neck: 1.8, arms: 1.3, legs: 0.85}

    Applied to the neutral pose, so every pose derived from it inherits the
    proportions and the character stays consistent across frames.
    """
    factors = {k: float(v) for k, v in (proportions or {}).items() if v}
    if not factors:
        return rig

    unknown = set(factors) - set(PROPORTION_GROUPS)
    if unknown:
        raise KeyError(
            f"unknown proportion group(s) {sorted(unknown)}. "
            f"Available: {', '.join(sorted(PROPORTION_GROUPS))}"
        )

    import math

    neutral = {k: list(v) for k, v in rig.neutral.items()}

    # Outward from the root, so a parent is already in its final place before
    # its children are moved relative to it.
    queue = [rig.root]
    while queue:
        parent = queue.pop(0)
        for child in rig.tree.get(parent, ()):
            if child not in neutral or parent not in neutral:
                continue
            group = _group_of(parent, child)
            factor = factors.get(group or "", 1.0)
            if factor != 1.0:
                px, py, pz = neutral[parent]
                cx, cy, cz = neutral[child]
                dx, dy, dz = cx - px, cy - py, cz - pz
                nx, ny, nz = px + dx * factor, py + dy * factor, pz + dz * factor
                shift = (nx - cx, ny - cy, nz - cz)
                # Everything below the child moves with it, or the limb detaches.
                stack = [child]
                while stack:
                    node = stack.pop()
                    if node in neutral:
                        neutral[node] = [
                            neutral[node][0] + shift[0],
                            neutral[node][1] + shift[1],
                            neutral[node][2] + shift[2],
                        ]
                    stack.extend(rig.tree.get(node, ()))
            queue.append(child)

    described = ", ".join(f"{k} x{v:g}" for k, v in sorted(factors.items()))
    # Head "size" is mostly the depth-map skull, not the tiny nose-to-ear bones.
    head_radius = rig.head_radius * factors.get("head", 1.0)

    # Bones get a little thicker when they get longer, and only a little.
    #
    # Leaving thickness alone was wrong in a visible way: legs lengthened 1.75x
    # kept their 0.055 capsule width and came out proportionally 1.75x thinner
    # than the rig they were derived from, so the depth map read as a stick
    # insect and the step between thigh and shin widths became a seam.
    #
    # Scaling thickness by the full factor is wrong in the other direction - a
    # taller character is not a wider one, and "lanky" is the point. The 0.35
    # exponent is the compromise: a 1.75x limb gets 1.21x width, which keeps
    # the slenderness while stopping the capsule from disappearing.
    bones = tuple(
        (a, b, thickness * (factors.get(_group_of(a, b) or "", 1.0) ** THICKNESS_EXPONENT))
        for a, b, thickness in rig.bones
    )
    return Rig(
        name=rig.name, label=f"{rig.label} ({described})",
        joints=rig.joints, tree=rig.tree, root=rig.root, neutral=neutral,
        bones=bones, skeleton_control=rig.skeleton_control,
        depth_control=rig.depth_control, face_joints=rig.face_joints,
        head_joint=rig.head_joint, head_radius=head_radius,
        prompt_hint=rig.prompt_hint, note=rig.note,
    )


# ------------------------------------------------------------------- none

# A pipeline that only needs "the same subject, seen from four sides" does not
# need a skeleton at all — and forcing one on a treasure chest, a tree or a
# creature whose anatomy you have not decided yet is friction with no payoff.
# Views become prompt terms instead of control images.
NONE = Rig(
    name="none",
    label="No rig (views by prompt)",
    joints=(),
    tree={},
    root="",
    neutral={},
    bones=(),
    skeleton_control=None,
    depth_control=None,
    face_joints=(),
    head_joint="",
    prompt_hint="",
    note="No skeleton or depth control. Views are steered by the prompt alone.",
)


_SOURCE: Decorated[Rig] = Decorated()
for _r in (NONE, HUMANOID, QUADRUPED, SERPENT, BLOB):
    _SOURCE.add(_r.name, _r, what="rig")
for _r in _GENERATED:
    # A generated rig yields to a hand-written one of the same name.
    _SOURCE.entries.setdefault(_r.name, _r)

_RIGS: Registry[Rig] = Registry("rig", _SOURCE)
REGISTRY: dict[str, Rig] = _SOURCE.entries      # kept: read directly in places

DEFAULT = HUMANOID.name


def get(name: str | None) -> Rig:
    return _RIGS.get(name or DEFAULT)


def names() -> list[str]:
    return _RIGS.names()


def summaries() -> list[dict]:
    return [
        {
            "name": key, "label": r.label, "joints": len(r.joints),
            "skeleton_control": r.skeleton_control, "note": r.note,
            "prompt_hint": r.prompt_hint,
        }
        for key, r in sorted(REGISTRY.items(), key=lambda kv: (kv[1].label))
    ]


def _swing(pose: dict[str, list[float]], root_joint: str,
           chain: tuple[str, ...], degrees: float, side: int) -> None:
    """Rotate a limb chain rigidly about its root, in the lateral/height plane.

    Rigid is the whole point. Placing each joint along a ray from the root
    preserves root-to-joint distances and silently rescales everything past
    the first bone, and assigning a coordinate outright preserves nothing.
    Both were shipped here before this existed.
    """
    import math

    if not chain or root_joint not in pose:
        return
    root = pose[root_joint]
    tip = pose[chain[-1]]
    current = math.atan2(side * (tip[0] - root[0]), tip[2] - root[2])
    delta = math.radians(degrees) - current
    sin, cos = math.sin(delta), math.cos(delta)

    for joint in chain:
        ox = side * (pose[joint][0] - root[0])
        oz = pose[joint][2] - root[2]
        pose[joint] = [
            root[0] + side * (ox * cos + oz * sin),
            pose[joint][1],
            root[2] + (-ox * sin + oz * cos),
        ]


# How far apart the feet stand in a symmetric reference pose, from vertical.
STANCE_DEGREES = 6.0

# Degrees from vertical for the limbs of a reference pose. 40 is the A-pose
# every character pipeline settles on, and it is a compromise between two
# measured failures rather than a convention borrowed on faith.
A_POSE_DEGREES = 40.0


def tpose(rig: Rig, symmetric: bool = False, spread: float | None = None
          ) -> dict[str, list[float]]:
    """The reference pose a character sheet is drawn in.

    Synthesised from the rig rather than stored as one file per creature.

    The arm angle is the whole design, and both extremes were tried:

      88 degrees (a true T)   Two horizontal limbs at shoulder height. A prompt
                              mentioning a sword or a staff comes back with the
                              blade drawn along an arm — the model reads a long
                              horizontal element as the thing you named.

       4 degrees (arms down)  Natural, and what references usually show. But
                              the arms lie against the torso: measured on the
                              humanoid rig this produced seven joint pairs
                              landing within 4% of the canvas of each other at
                              front view, so the silhouette has no gap between
                              limb and body and neither a person nor a model
                              can tell where the arm ends.

      40 degrees (an A-pose)  Limbs clear of the torso, so the silhouette
                              reads, without a horizontal to mistake for a
                              weapon. This is why every game-art pipeline
                              authors its rest pose this way.

    `symmetric` mirrors the two sides. It is off by default because a
    reference photograph is rarely symmetric, and forcing a rigid A-frame asks
    the model to reconcile the pose with evidence that contradicts it. The
    spread applies either way — an asymmetric pose still needs its limbs clear
    of the body.
    """
    import math

    pose = {k: list(v) for k, v in rig.neutral.items()}
    angle = A_POSE_DEGREES if spread is None else float(spread)

    arms = [j for j in rig.joints if j.endswith("_shoulder") or "_shoulder_" in j]
    for shoulder in arms:
        side = 1 if shoulder.startswith("l_") else -1
        elbow = shoulder.replace("shoulder", "elbow")
        wrist = shoulder.replace("shoulder", "wrist")
        if elbow not in pose or wrist not in pose:
            continue

        _swing(pose, shoulder, (elbow, wrist), angle, side)

    if not symmetric:
        return pose

    # A wider stance, by rotating the leg rather than dragging the foot
    # sideways. Assigning the ankle's x directly is what this used to do, and
    # it stretched the shin: measured, knee-to-ankle grew from 0.1600 to
    # 0.1607 every time a symmetric sheet was generated.
    for hip, knee, ankle in (("l_hip", "l_knee", "l_ankle"),
                             ("r_hip", "r_knee", "r_ankle")):
        if hip in pose and ankle in pose:
            side = 1 if hip.startswith("l_") else -1
            _swing(pose, hip, tuple(j for j in (knee, ankle) if j in pose),
                   STANCE_DEGREES, side)
    return pose


def infer_from_pose(pose: dict) -> Rig:
    """Best-effort: which rig do these joint names belong to?

    Pose files written before rigs existed have no rig field, so the joint
    names are the only evidence available.
    """
    keys = set(pose or {})
    best, best_score = REGISTRY[DEFAULT], -1.0
    for rig in REGISTRY.values():
        overlap = len(keys & set(rig.joints))
        score = overlap / max(len(rig.joints), 1)
        if score > best_score:
            best, best_score = rig, score
    return best
