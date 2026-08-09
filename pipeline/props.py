"""Held objects: swords, staves, shields, capes.

A prop attaches to a joint and inherits that joint's motion, so a sword swung
through an attack traces the arc the arm already describes and needs no
separate authoring.

Props deliberately do not become skeleton joints. The OpenPose ControlNet was
trained on eighteen human joints, and an extra chain hanging off a wrist reads
to it as a malformed limb — the same failure that made non-humanoid rigs
impossible. Instead a prop is drawn into the depth map, which we render
ourselves and which has no opinion about anatomy, and named in the prompt.

That split matters for a failure we measured: told only "holding a sword", the
model decides for itself where the blade goes, and sometimes puts two of them
sticking out sideways. A socketed prop occupies a definite volume in the depth
map, so there is a place for the blade to be.

Flexible props — a whip, a chain, a cape — set `flex` above zero and sag along
their length rather than staying rigid.

The honest limit: a prop is drawn as a tapered line, which suits anything
long and thin — swords, staves, spears, tails, whips. A broad cloak is a
surface rather than a line, and comes out as a slab. For cloth that needs to
read as cloth, soft-body nodes deform the generated sprite itself and do a
better job than any depth-map primitive would.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from .bodyspace import project_point


@dataclass
class Prop:
    """An object held at, or hanging from, a joint."""

    name: str
    socket: str                                     # joint it attaches to
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # Direction the prop extends, in body space. Normalised on use.
    aim: tuple[float, float, float] = (0.0, 0.35, -1.0)
    length: float = 0.30
    width: float = 0.022
    # A second socket the prop is also held by. Two-handed weapons and bows
    # read wrong unless the off hand is pulled toward the grip.
    second_socket: str = ""
    prompt: str = ""
    flex: float = 0.0                               # 0 rigid, >0 soft chain
    segments: int = 4                               # only used when flex > 0
    influence: float = 1.0
    shade: float = 1.0                              # depth brightness multiplier

    @classmethod
    def from_config(cls, entry: dict) -> "Prop":
        known = set(cls.__dataclass_fields__)
        unknown = set(entry) - known
        if unknown:
            raise KeyError(
                f"prop '{entry.get('name', '?')}' has unknown key(s) "
                f"{sorted(unknown)}. Valid: {sorted(known)}"
            )
        data = dict(entry)
        for key in ("offset", "aim"):
            if key in data and data[key] is not None:
                data[key] = tuple(float(v) for v in data[key])
        return cls(**data)


DIRNAME = "props"


def discover(root) -> dict[str, dict]:
    """Reusable prop definitions from props/*.yaml, keyed by name.

    poses and palettes have had libraries since the beginning and props did
    not, so every weapon had to be dimensioned by hand in the config that used
    it — six numbers describing where a sword points, retyped per character.
    A prop is exactly as reusable as a palette: a longsword is a longsword.
    """
    import yaml

    base = root / DIRNAME
    if not base.exists():
        return {}
    found: dict[str, dict] = {}
    for path in sorted(base.rglob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            continue
        for entry in (data.get("props") or []):
            if isinstance(entry, dict) and entry.get("name"):
                found[entry["name"]] = entry
    return found


def load(specs: Sequence[dict | str] | None, root=None) -> list[Prop]:
    """Build props from a config block.

    An entry may be a bare name from the library, or a dict. A dict carrying a
    `from` key starts from the library entry and overrides it, so "a longsword
    but shorter" costs one line instead of seven.
    """
    library = discover(root) if root is not None else {}
    out: list[Prop] = []
    for spec in (specs or []):
        if not spec:
            continue
        if isinstance(spec, str):
            if spec not in library:
                raise KeyError(
                    f"no prop '{spec}' in the library. Available: "
                    f"{', '.join(sorted(library)) or '(none)'}")
            entry = dict(library[spec])
        elif spec.get("from"):
            base_name = spec["from"]
            if base_name not in library:
                raise KeyError(
                    f"prop '{spec.get('name', base_name)}' extends '{base_name}', "
                    f"which is not in the library. Available: "
                    f"{', '.join(sorted(library)) or '(none)'}")
            entry = {**library[base_name], **{k: v for k, v in spec.items() if k != "from"}}
        else:
            entry = dict(spec)
        out.append(Prop.from_config(entry))
    return out


def _normalise(vec: Sequence[float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(v * v for v in vec))
    if length < 1e-9:
        return (0.0, 0.0, -1.0)
    return tuple(v / length for v in vec)  # type: ignore[return-value]


def anchor(prop: Prop, pose: dict, rig) -> tuple[float, float, float] | None:
    """Where the prop's grip sits in body space, for this pose."""
    base = pose.get(prop.socket) or rig.neutral.get(prop.socket)
    if base is None:
        return None
    return tuple(base[i] + prop.offset[i] for i in range(3))  # type: ignore[return-value]


def tip(prop: Prop, pose: dict, rig) -> tuple[float, float, float] | None:
    """The far end of a rigid prop.

    Aim follows the limb it is held by where that can be worked out: a sword
    points the way the forearm points, so a swing carries the blade with it
    instead of leaving it stuck at a fixed angle.
    """
    grip = anchor(prop, pose, rig)
    if grip is None:
        return None

    direction = prop.aim
    parent = None
    for candidate, children in rig.tree.items():
        if prop.socket in children:
            parent = candidate
            break
    if parent and parent in pose and prop.socket in pose:
        limb = [pose[prop.socket][i] - pose[parent][i] for i in range(3)]
        if any(abs(v) > 1e-6 for v in limb):
            # Blend the limb direction with the authored aim, so a prop keeps
            # some of its intended angle while still following the arm.
            unit = _normalise(limb)
            aim = _normalise(prop.aim)
            direction = tuple(unit[i] * 0.65 + aim[i] * 0.35 for i in range(3))

    unit = _normalise(direction)
    return tuple(grip[i] + unit[i] * prop.length for i in range(3))  # type: ignore[return-value]


def chain(prop: Prop, pose: dict, rig) -> list[tuple[float, float, float]]:
    """Points along a prop, one segment for a rigid one, several when flexible."""
    grip = anchor(prop, pose, rig)
    end = tip(prop, pose, rig)
    if grip is None or end is None:
        return []
    if prop.flex <= 0:
        return [grip, end]

    points = []
    for i in range(prop.segments + 1):
        t = i / prop.segments
        # A flexible prop sags toward the ground, more so further along.
        sag = prop.flex * prop.length * (t ** 2)
        points.append((
            grip[0] + (end[0] - grip[0]) * t,
            grip[1] + (end[1] - grip[1]) * t,
            grip[2] + (end[2] - grip[2]) * t + sag,
        ))
    return points


def draw_depth(draw, props: Sequence[Prop], pose: dict, rig, yaw: float,
               width: int, height: int, shade_of, depth_scale: float = 1.0,
               lateral_scale: float = 1.0, floor: int = 60) -> int:
    """Add props to a depth map. Returns how many were drawn.

    `floor` is the darkest value the body itself uses. A prop must not go
    below it: black means background, so a dim cape would read as a hole
    punched through the sprite rather than cloth hanging behind it.
    """
    drawn = 0
    for prop in props:
        if prop.influence <= 0:
            continue
        points = chain(prop, pose, rig)
        if len(points) < 2:
            continue

        span = min(width, height)
        for index, (a, b) in enumerate(zip(points, points[1:])):
            # Taper along the chain. A rigid blade keeps its width; a cape or
            # whip narrows toward the end, which is what stops a flexible prop
            # reading as a rectangle bolted to the figure.
            t = index / max(len(points) - 2, 1)
            taper = 1.0 if prop.flex <= 0 else (1.0 - 0.45 * t)
            thickness = max(2.0, prop.width * span * taper)
            pa = project_point(a, yaw, depth_scale=depth_scale, lateral_scale=lateral_scale)
            pb = project_point(b, yaw, depth_scale=depth_scale, lateral_scale=lateral_scale)
            # Depth from the midpoint, so a prop pointing at the camera reads
            # as nearer than one pointing away.
            mid_depth = (a[1] + b[1]) / 2 * math.cos(math.radians(yaw)) \
                + (a[0] + b[0]) / 2 * math.sin(math.radians(yaw))
            # Dim within the occupied range rather than toward black.
            base = shade_of(mid_depth)
            value = int(max(floor, min(255, floor + (base - floor) * prop.shade)))
            draw.line(
                [pa[0] * width, pa[1] * height, pb[0] * width, pb[1] * height],
                fill=value, width=int(thickness),
            )
        drawn += 1
    return drawn


def pull_second_hand(props: Sequence[Prop], pose: dict, rig) -> dict:
    """Move an off hand onto a two-handed prop's grip.

    Without this a greatsword or a bow is held in one hand while the other
    stays wherever the pose left it, which reads as wrong immediately.
    """
    out = {k: list(v) for k, v in pose.items()}
    for prop in props:
        if not prop.second_socket or prop.second_socket not in out:
            continue
        grip = anchor(prop, out, rig)
        end = tip(prop, out, rig)
        if grip is None or end is None:
            continue
        # A shade along the shaft, not on top of the primary hand.
        hold = tuple(grip[i] + (end[i] - grip[i]) * 0.22 for i in range(3))
        out[prop.second_socket] = list(hold)
    return out


def prompt_terms(props: Sequence[Prop]) -> str:
    """What the props add to the prompt, for the ones that named something."""
    said = [p.prompt.strip() for p in props if p.prompt and p.influence > 0]
    return ", ".join(said)


def describe(props: Sequence[Prop]) -> str:
    if not props:
        return "no props"
    return "; ".join(
        f"{p.name} on {p.socket}" + (" (2h)" if p.second_socket else "")
        + (f" flex {p.flex:g}" if p.flex else "")
        for p in props
    )
