
from __future__ import annotations
from ..shared import paths

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..geometry.bodyspace import VIEWS, resolve_view
from ..shared.errors import Invalid, NotFound

ROLES = ("identity", "style", "pose", "palette")

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
    annotation: str = ""

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
        raise Invalid(f"references.{role}[{index}] needs at least a 'path'",
                      field="path")

    path = (root / entry["path"]).resolve()
    if not path.exists():
        raise NotFound("reference image", entry["path"])

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
        raise Invalid(
            "references.images was replaced by typed roles. Use "
            "references.identity for who the character is, references.style "
            "for the art style to imitate, references.pose for a composition "
            "to match, and references.palette for colours.",
            field="images",
        )

    lib = Library()
    for role in ROLES:
        entries = cfg.get(role) or []
        if isinstance(entries, (str, dict)):
            entries = [entries]
        setattr(lib, role, [_one(root, e, role, i) for i, e in enumerate(entries)])

    lib.identity.extend(from_pattern(root, cfg, lib.identity))
    lib.identity.extend(fill_missing_sides(cfg, lib.identity))
    lib = _merge_from_run(root, cfg, lib)
    return lib


VIEW_ALIASES: dict[str, float] = {
    "front": 0.0,
    "back": 180.0, "rear": 180.0,
    "side": 90.0, "side_left": 90.0, "left": 90.0,
    "side_right": 270.0, "right": 270.0,
}


def from_pattern(root: Path, cfg: dict, existing: list[Reference]) -> list[Reference]:

    pattern = cfg.get("pattern")
    if not pattern:
        return []

    name = cfg.get("_name") or cfg.get("name") or ""
    have = {round(r.yaw) % 360 for r in existing}
    found: list[Reference] = []

    for label, yaw in VIEW_ALIASES.items():
        if round(yaw) % 360 in have:
            continue
        rel = str(pattern).replace("{view}", label) \
                          .replace("{name}", str(name)).replace("{id}", str(name))
        path = (root / rel) if not Path(rel).is_absolute() else Path(rel)
        if not path.is_file():
            continue
        found.append(Reference(path=path, role="identity", yaw=yaw, label=label))
        have.add(round(yaw) % 360)

    return found


def _mirrored(src: Reference, yaw: float) -> Reference | None:
    """A horizontally flipped copy of `src`, cached beside the original."""
    from PIL import Image

    dst = src.path.with_name(f"{src.path.stem}__mirror{src.path.suffix}")
    try:
        if not dst.exists() or dst.stat().st_mtime < src.path.stat().st_mtime:
            Image.open(src.path).transpose(Image.FLIP_LEFT_RIGHT).save(dst)
    except Exception:
        return None
    return Reference(path=dst, role="identity", yaw=yaw,
                     label=f"{src.label}-mirrored", weight_scale=src.weight_scale)


def fill_missing_sides(cfg, existing: list[Reference]) -> list[Reference]:
    """Without this a 270 frame with only a 90 reference takes it at far_weight (0.45) - deliberately weak, because pick() gives latitude where there is no evidence."""
    match = cfg.get("match") or {}
    mode = str(match.get("side_fallback", "none")).lower()
    if mode == "none":
        return []

    have = {round(r.yaw) % 360 for r in existing}
    by_yaw = {round(r.yaw) % 360: r for r in existing}
    out: list[Reference] = []

    for yaw, other in ((90.0, 270), (270.0, 90)):
        if round(yaw) % 360 in have:
            continue
        src = None
        if mode == "mirror":
            src = by_yaw.get(other)
            if src is not None:
                # Re-pointing the left image at 270 without mirroring would hand the model a left-facing figure labelled as the right side [...]
                src = _mirrored(src, yaw)
        elif mode in ("back", "rear"):
            src = by_yaw.get(180)
        elif mode == "front":
            src = by_yaw.get(0)
        if src is None:
            continue
        out.append(Reference(path=src.path, role="identity", yaw=yaw,
                             label=f"{src.label}->{int(yaw)}",
                             weight_scale=src.weight_scale))
    return out


def _merge_from_run(root: Path, cfg: dict, lib: Library) -> Library:
    run_id = cfg.get("from_run")
    if not run_id:
        return lib

    base = Path(cfg.get("_runs_dir") or paths.resolve(root, "runs")) / run_id
    if not base.is_dir():
        raise NotFound("run", run_id, hint=f"not a run in {base.parent}")

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
        raise Invalid(f"run '{run_id}' has no generated frames to reference",
                      field="from_run")

    if yaws and len(yaws) != len(images):
        yaws = []

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
    if not refs:
        raise Invalid("no references to choose from", field="references")

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
