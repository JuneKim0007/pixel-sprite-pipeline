

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

from . import rigs
from ..definitive.pixelize import background_to_alpha


@dataclass
class Fit:
    points: dict[str, list[float]] = field(default_factory=dict)
    proportions: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "points": self.points,
            "proportions": self.proportions,
            "confidence": round(self.confidence, 2),
            "notes": self.notes,
        }


def subject_mask(image: Path, tolerance: int = 18) -> np.ndarray:
    """Boolean mask of the sprite, background keyed out from the edges.

    Reuses the same flood fill the palette stage uses, so what counts as
    background here is what counts as background there.
    """
    rgb = np.asarray(Image.open(image).convert("RGB"))
    keyed = background_to_alpha(rgb, tolerance)
    mask = keyed[..., 3] > 0

    # Drop specks so a stray pixel does not widen the profile.
    labels, count = ndimage.label(mask)
    if count > 1:
        sizes = ndimage.sum(mask, labels, range(1, count + 1))
        mask = labels == (int(np.argmax(sizes)) + 1)
    return mask


def _profile(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-row width and horizontal centre of the subject."""
    widths = mask.sum(axis=1).astype(float)
    centres = np.full(mask.shape[0], np.nan)
    for y in range(mask.shape[0]):
        xs = np.nonzero(mask[y])[0]
        if len(xs):
            centres[y] = (xs.min() + xs.max()) / 2
    return widths, centres


def _extent(mask: np.ndarray, y: int) -> tuple[int, int] | None:
    xs = np.nonzero(mask[y])[0]
    return (int(xs.min()), int(xs.max())) if len(xs) else None


def fit_humanoid(mask: np.ndarray, rig=None) -> Fit:
    """Read a standing humanoid's joints off its width profile."""
    rig = rig or rigs.HUMANOID
    fit = Fit()
    h, w = mask.shape

    rows = np.nonzero(mask.any(axis=1))[0]
    if len(rows) < 12:
        fit.notes.append("subject too small to fit")
        return fit
    top, bottom = int(rows.min()), int(rows.max())
    height = bottom - top
    if height < 12:
        fit.notes.append("subject too short to fit")
        return fit

    widths, centres = _profile(mask)
    band = widths[top:bottom + 1]
    if band.max() <= 0:
        return fit

    def at(fraction: float) -> int:
        return int(top + fraction * height)

    def px(y: int, x: float) -> list[float]:
        return [float(x) / w, float(y) / h]

    def centre_at(y: int) -> float:
        value = centres[max(top, min(bottom, y))]
        return float(value) if not math.isnan(value) else w / 2

    # Shoulders: the first sharp WIDENING below the head, not simply the widest
    # row up top. A character in a skirt or a cloak is wider at the hem than at
    # the shoulders, and picking the maximum lands the joint on the belt.
    smooth = np.convolve(band, np.ones(max(3, height // 40)) / max(3, height // 40),
                         mode="same")
    search_lo, search_hi = int(height * 0.08), int(height * 0.40)
    slope = np.diff(smooth[:max(search_hi + 1, search_lo + 2)])
    shoulder_rel = None
    if len(slope) > search_lo + 1:
        window = slope[search_lo:]
        if len(window):
            shoulder_rel = search_lo + int(np.argmax(window)) + 1
    if shoulder_rel is None or shoulder_rel >= height:
        shoulder_rel = int(height * 0.20)
        fit.notes.append("no clear head-to-shoulder step; used a default height")
    shoulder_y = top + shoulder_rel
    shoulder_span = _extent(mask, shoulder_y)

    # Head: the narrow region above the shoulders.
    head_band = band[: max(1, shoulder_y - top)]
    head_y = top + int(np.argmin(head_band)) if len(head_band) > 2 else at(0.08)
    nose_y = top + int(height * 0.11)

    # Hips: widest row in the middle band, below the waist pinch.
    lower_start = int(height * 0.42)
    lower_end = int(height * 0.72)
    mid = band[lower_start:lower_end]
    hip_y = top + lower_start + (int(np.argmax(mid)) if len(mid) else int(height * 0.15))
    hip_span = _extent(mask, hip_y)

    # Legs: where the silhouette splits in two, the gap gives each leg's centre.
    ankle_y = bottom - max(1, int(height * 0.03))
    knee_y = int((hip_y + ankle_y) / 2)

    def split_centres(y: int) -> tuple[float, float]:
        xs = np.nonzero(mask[max(top, min(bottom, y))])[0]
        if len(xs) == 0:
            c = centre_at(y)
            return c - w * 0.02, c + w * 0.02
        gaps = np.nonzero(np.diff(xs) > 1)[0]
        if len(gaps):
            cut = gaps[len(gaps) // 2]
            left, right = xs[: cut + 1], xs[cut + 1:]
            return float(left.mean()), float(right.mean())
        c = float(xs.mean())
        quarter = (xs.max() - xs.min()) / 4 or w * 0.02
        return c - quarter, c + quarter

    knee_l, knee_r = split_centres(knee_y)
    ankle_l, ankle_r = split_centres(ankle_y)

    centre_x = centre_at(shoulder_y)
    if shoulder_span:
        half = (shoulder_span[1] - shoulder_span[0]) / 2
    else:
        half = w * 0.08
    hip_half = ((hip_span[1] - hip_span[0]) / 2 if hip_span else half * 0.7)

    torso = max(hip_y - shoulder_y, int(height * 0.15))
    elbow_y = min(bottom, shoulder_y + int(torso * 0.55))
    wrist_y = min(bottom, shoulder_y + int(torso * 1.05))
    elbow_span = _extent(mask, elbow_y)
    wrist_span = _extent(mask, wrist_y)

    fit.points = {
        "nose": px(nose_y, centre_at(nose_y)),
        "neck": px(shoulder_y - int(height * 0.02), centre_x),
        "l_shoulder": px(shoulder_y, centre_x + half * 0.78),
        "r_shoulder": px(shoulder_y, centre_x - half * 0.78),
        "l_elbow": px(elbow_y, (elbow_span[1] if elbow_span else centre_x + half) - w * 0.01),
        "r_elbow": px(elbow_y, (elbow_span[0] if elbow_span else centre_x - half) + w * 0.01),
        "l_wrist": px(wrist_y, (wrist_span[1] if wrist_span else centre_x + half) - w * 0.01),
        "r_wrist": px(wrist_y, (wrist_span[0] if wrist_span else centre_x - half) + w * 0.01),
        "l_hip": px(hip_y, centre_at(hip_y) + hip_half * 0.5),
        "r_hip": px(hip_y, centre_at(hip_y) - hip_half * 0.5),
        "l_knee": px(knee_y, knee_r),
        "r_knee": px(knee_y, knee_l),
        "l_ankle": px(ankle_y, ankle_r),
        "r_ankle": px(ankle_y, ankle_l),
    }

    # Confidence from how clearly the profile actually showed the landmarks a
    # humanoid should have. A featureless blob scores low and says so.
    signals = []
    signals.append(min(1.0, height / (h * 0.45)))
    signals.append(0.35 if any("head-to-shoulder" in n for n in fit.notes) else 1.0)
    waist = band[int(height * 0.30):int(height * 0.45)]
    if len(waist) and band.max() > 0:
        pinch = 1.0 - waist.min() / band.max()
        signals.append(min(1.0, pinch * 2.2))
    if abs(ankle_r - ankle_l) > w * 0.01:
        signals.append(1.0)
    else:
        signals.append(0.2)
        fit.notes.append("legs did not separate; the fit may be a single mass")
    fit.confidence = float(np.mean(signals))

    fit.proportions = measure_proportions(fit.points, rig)
    return fit


def measure_proportions(points: dict[str, list[float]], rig) -> dict[str, float]:
    """Bone-group ratios against the rig's defaults, scaled by torso height."""
    if "neck" not in points or "l_hip" not in points:
        return {}

    def observed(a: str, b: str) -> float | None:
        if a not in points or b not in points:
            return None
        return math.dist(points[a], points[b])

    def expected(a: str, b: str) -> float | None:
        if a not in rig.neutral or b not in rig.neutral:
            return None
        pa, pb = rig.neutral[a], rig.neutral[b]
        return math.dist((pa[0], pa[2]), (pb[0], pb[2]))

    torso_o, torso_e = observed("neck", "l_hip"), expected("neck", "l_hip")
    if not torso_o or not torso_e:
        return {}
    scale = torso_o / torso_e

    groups: dict[str, list[float]] = {}
    for a, b, _w in rig.bones:
        o, e = observed(a, b), expected(a, b)
        if not o or not e:
            continue
        group = rigs._group_of(a, b)
        if group:
            groups.setdefault(group, []).append((o / scale) / e)

    out = {}
    for group, samples in groups.items():
        samples.sort()
        median = samples[len(samples) // 2]
        if 0.4 <= median <= 2.5 and abs(median - 1.0) > 0.15:
            out[group] = round(median, 2)
    return out


def propose(image: Path, rig_name: str = "humanoid", tolerance: int = 18) -> Fit:
    """Fit a rig to a sprite. Humanoid only for now; others return proportions."""
    rig = rigs.get(rig_name)
    mask = subject_mask(image, tolerance)

    if not mask.any():
        return Fit(notes=["nothing found after keying out the background"])

    if rig_name == "humanoid":
        return fit_humanoid(mask, rig)

    # Other body plans have no reliable profile signature — a spider's width
    # profile says nothing about which lobe is which leg. Report the bounding
    # geometry so the editor can at least scale the template sensibly.
    rows = np.nonzero(mask.any(axis=1))[0]
    cols = np.nonzero(mask.any(axis=0))[0]
    fit = Fit(confidence=0.25)
    fit.notes.append(
        f"no profile fit for '{rig_name}'; only the bounding box was measured"
    )
    fit.proportions = {}
    if len(rows) and len(cols):
        aspect = (cols.max() - cols.min()) / max(rows.max() - rows.min(), 1)
        fit.notes.append(f"subject aspect ratio {aspect:.2f}")
    return fit
