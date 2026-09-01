

from __future__ import annotations

from ..shared import contracts, paths
from ..shared import settings

import math
from pathlib import Path
from dataclasses import dataclass
from typing import Sequence

from .bodyspace import project_point
from ..shared.errors import Invalid, NotFound
from ..shared.registry import Broken, Registry, Scanned


@dataclass
class Prop:
    """An object held at, or hanging from, a joint."""

    name: str
    socket: str
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    aim: tuple[float, float, float] = (0.0, 0.35, -1.0)
    length: float = 0.30
    width: float = 0.022
    second_socket: str = ""
    prompt: str = ""
    flex: float = 0.0
    segments: int = 4
    influence: float = 1.0
    shade: float = 1.0

    @classmethod
    def from_config(cls, entry: dict) -> "Prop":
        return contracts.from_entry(cls, entry, noun="prop",
                                    tuples=("offset", "aim"))


DIRNAME = "props"

_REGISTRIES: dict[Path, Registry] = {}


def wanted(config: dict) -> bool:
    from ..shared.config import opt

    block = config.get("props")
    if isinstance(block, dict):
        explicit = opt(block, "enabled", None)
        if explicit is not None:
            return bool(explicit)
    explicit = config.get("props_enabled")
    if explicit is not None:
        return bool(explicit)
    return config.get("module", "animation") != "character_sheet"


def registry(root) -> Registry[dict]:
    root = Path(root).resolve()
    found = _REGISTRIES.get(root)
    if found is None:
        found = Registry("prop", Scanned(paths.resolve(root, "props"), ["**/*.yaml"],
                                         _entries, what="prop"))
        _REGISTRIES[root] = found
    return found


def _entries(path: Path) -> dict[str, dict]:
    """Every prop in one file. A malformed one names itself now."""

    data = settings.read_yaml(path)
    listed = data.get("props")
    if listed is None:
        return {}
    if not isinstance(listed, list):
        raise Invalid("'props:' should be a list")

    out: dict[str, dict] = {}
    for i, entry in enumerate(listed):
        if not isinstance(entry, dict):
            raise Invalid(f"prop {i + 1} is not a mapping")
        name = entry.get("name")
        if not name:
            raise Invalid(f"prop {i + 1} has no 'name'")
        out[str(name)] = entry
    return out


def discover(root) -> dict[str, dict]:
    return registry(root).all()


def broken(root) -> list[Broken]:
    """Prop files that would not load."""
    return registry(root).broken()


def load(specs, root=None) -> list[Prop]:
    if isinstance(specs, dict):
        specs = specs.get("items") or specs.get("props") or []
    library = discover(root) if root is not None else {}
    out: list[Prop] = []
    for spec in (specs or []):
        if not spec:
            continue
        if isinstance(spec, str):
            if spec not in library:
                raise NotFound("prop", spec, available=list(library))
            entry = dict(library[spec])
        elif spec.get("from"):
            base_name = spec["from"]
            if base_name not in library:
                raise NotFound(
                    f"prop that '{spec.get('name', base_name)}' extends",
                    base_name, available=list(library))
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
    drawn = 0
    for prop in props:
        if prop.influence <= 0:
            continue
        points = chain(prop, pose, rig)
        if len(points) < 2:
            continue

        span = min(width, height)
        for index, (a, b) in enumerate(zip(points, points[1:])):
            t = index / max(len(points) - 2, 1)
            taper = 1.0 if prop.flex <= 0 else (1.0 - 0.45 * t)
            thickness = max(2.0, prop.width * span * taper)
            pa = project_point(a, yaw, depth_scale=depth_scale, lateral_scale=lateral_scale)
            pb = project_point(b, yaw, depth_scale=depth_scale, lateral_scale=lateral_scale)
            mid_depth = (a[1] + b[1]) / 2 * math.cos(math.radians(yaw)) \
                + (a[0] + b[0]) / 2 * math.sin(math.radians(yaw))
            base = shade_of(mid_depth)
            value = int(max(floor, min(255, floor + (base - floor) * prop.shade)))
            draw.line(
                [pa[0] * width, pa[1] * height, pb[0] * width, pb[1] * height],
                fill=value, width=int(thickness),
            )
        drawn += 1
    return drawn


def pull_second_hand(props: Sequence[Prop], pose: dict, rig) -> dict:
    out = {k: list(v) for k, v in pose.items()}
    for prop in props:
        if not prop.second_socket or prop.second_socket not in out:
            continue
        grip = anchor(prop, out, rig)
        end = tip(prop, out, rig)
        if grip is None or end is None:
            continue
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
