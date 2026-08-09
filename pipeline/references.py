"""Reference images, typed by the job they do.

A reference used to be one flat list labelled only by viewing angle, which
quietly conflated four different things. They are not interchangeable: an
illustration of your character and a good pixel-art sprite both "look like
what I want", but one says *who* and the other says *how*, and feeding either
into the other's slot produces the wrong sprite.

    identity   who the character is. Illustrations, concept art, photographs
               — anything, and deliberately not required to be pixel art.
               Matched to each frame by viewing angle, driven through
               IP-Adapter at a high weight.

    style      what the art should look like. Good sprites in the target
               idiom. Weak weight on purpose: a style reference at identity
               strength overwrites the character with the exemplar.

    pose       composition and framing to reproduce, via its annotation and
               the pose ControlNet.

    palette    colours to lock to, read once and imposed deterministically.

The weights differ by an order of magnitude between roles, which is the real
reason they cannot share a list.

One trap worth naming: identity references are usually illustrations, and
img2img from an illustration traces its rendering — gradients, soft edges,
anti-aliasing — which is the opposite of a sprite. Identity therefore goes
through IP-Adapter only, and the pixelation comes from generation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bodyspace import VIEWS, resolve_view

ROLES = ("identity", "style", "pose", "palette")

# Sensible strengths per role. Identity has to hold a character together;
# style only has to tint it.
DEFAULT_WEIGHT = {
    "identity": 0.80,
    "style": 0.35,
    "pose": 1.00,
    "palette": 1.00,
}


@dataclass
class Reference:
    path: Path
    role: str = "identity"
    yaw: float = 0.0
    label: str = "front"
    weight_scale: float = 1.0
    annotation: str = ""          # pose role: "auto" to use a saved annotation

    @property
    def base_weight(self) -> float:
        return DEFAULT_WEIGHT.get(self.role, 0.8)


@dataclass
class Library:
    """Every reference a run has, grouped by what it is for."""

    identity: list[Reference] = field(default_factory=list)
    style: list[Reference] = field(default_factory=list)
    pose: list[Reference] = field(default_factory=list)
    palette: list[Reference] = field(default_factory=list)

    def of(self, role: str) -> list[Reference]:
        return getattr(self, role, [])

    def any(self) -> bool:
        return any(self.of(r) for r in ROLES)

    def summary(self) -> str:
        parts = [f"{len(self.of(r))} {r}" for r in ROLES if self.of(r)]
        return ", ".join(parts) or "no references"


def angular_distance(a: float, b: float) -> float:
    """Shortest angle between two headings, in degrees (0-180)."""
    d = abs(a - b) % 360.0
    return 360.0 - d if d > 180.0 else d


def _one(root: Path, entry: Any, role: str, index: int) -> Reference:
    if isinstance(entry, str):
        entry = {"path": entry}
    if not isinstance(entry, dict) or "path" not in entry:
        raise ValueError(f"references.{role}[{index}] needs at least a 'path'")

    path = (root / entry["path"]).resolve()
    if not path.exists():
        raise FileNotFoundError(f"reference image not found: {entry['path']}")

    label = str(entry.get("view", "front"))
    return Reference(
        path=path,
        role=role,
        yaw=resolve_view(label),
        label=label,
        weight_scale=float(entry.get("weight", entry.get("weight_scale", 1.0))),
        annotation=str(entry.get("annotation", "")),
    )


def load(root: Path, cfg: dict | None) -> Library:
    """Build the library from a config's `references:` block."""
    cfg = cfg or {}

    if "images" in cfg:
        raise ValueError(
            "references.images was replaced by typed roles. Use "
            "references.identity for who the character is, references.style "
            "for the art style to imitate, references.pose for a composition "
            "to match, and references.palette for colours."
        )

    lib = Library()
    for role in ROLES:
        entries = cfg.get(role) or []
        if isinstance(entries, (str, dict)):
            entries = [entries]
        setattr(lib, role, [_one(root, e, role, i) for i, e in enumerate(entries)])

    lib = _merge_from_run(root, cfg, lib)
    return lib


def _merge_from_run(root: Path, cfg: dict, lib: Library) -> Library:
    """Adopt a previous run's output as identity and palette references.

    This is what joins the two generators: a character sheet already records
    the angle it rendered each view at, so an animation can inherit those views
    without anyone retyping paths or re-labelling front from rear.
    """
    run_id = cfg.get("from_run")
    if not run_id:
        return lib

    base = Path(cfg.get("_runs_dir") or (root / "out" / "runs")) / run_id
    if not base.is_dir():
        raise FileNotFoundError(
            f"references.from_run points at '{run_id}', which is not a run in "
            f"{base.parent}"
        )

    yaws: list[float] = []
    for pose_dir in sorted(base.glob("*_pose")):
        meta = pose_dir / "pose.json"
        if meta.exists():
            data = json.loads(meta.read_text())
            yaws = [float(e.get("yaw", 0.0)) for e in data.get("entries", [])]
            break

    images: list[Path] = []
    for suffix in ("_palette", "_softbody", "_frames"):
        for stage_dir in sorted(base.glob(f"*{suffix}")):
            found = sorted(p for p in stage_dir.glob("*.png") if p.suffix == ".png")
            if found:
                images = found
                break
        if images:
            break
    if not images:
        raise FileNotFoundError(f"run '{run_id}' has no generated frames to reference")

    if yaws and len(yaws) != len(images):
        yaws = []          # edited after the fact; better unlabelled than wrong

    for i, path in enumerate(images):
        yaw = yaws[i] if yaws else 0.0
        label = next((n for n, deg in VIEWS.items() if abs(deg - yaw) < 1e-6),
                     f"{yaw:g}deg")
        lib.identity.append(Reference(path=path, role="identity", yaw=yaw, label=label))

    palette_file = next(base.glob("*_palette/palette.hex"), None)
    if palette_file and not lib.palette:
        lib.palette.append(
            Reference(path=palette_file, role="palette", label="inherited"))
    return lib


def pick(
    refs: list[Reference],
    yaw: float,
    *,
    tolerance: float = 40.0,
    exact_weight: float = 0.85,
    far_weight: float = 0.45,
) -> tuple[Reference, float, float]:
    """Closest reference to `yaw`, and the weight to use with it.

    Weight falls off with angular distance, which is the opposite of the
    instinct. A front reference forced at full strength onto a rear generation
    produces a front-facing sprite that fights the pose; a weak hint leaves the
    model free to invent the side it has never seen. Constraint where there is
    evidence, latitude where there is not.
    """
    if not refs:
        raise ValueError("no references to choose from")

    best = min(refs, key=lambda r: angular_distance(r.yaw, yaw))
    dist = angular_distance(best.yaw, yaw)

    if dist <= tolerance:
        weight = exact_weight
    else:
        span = max(180.0 - tolerance, 1e-6)
        t = min((dist - tolerance) / span, 1.0)
        weight = exact_weight + (far_weight - exact_weight) * t

    return best, weight * best.weight_scale, dist


def explain(ref: Reference, weight: float, dist: float, tolerance: float) -> str:
    quality = "match" if dist <= tolerance else "nearest"
    return (f"{quality} '{ref.label}' ({ref.yaw:g}°, {dist:.0f}° away) "
            f"weight {weight:.2f}")


def style_weight(refs: list[Reference], configured: float | None = None) -> float:
    """Blended strength for style exemplars, kept deliberately low."""
    if not refs:
        return 0.0
    base = configured if configured is not None else DEFAULT_WEIGHT["style"]
    return min(0.6, base * max(r.weight_scale for r in refs))
