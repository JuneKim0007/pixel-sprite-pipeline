

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

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
    rgb = np.asarray(Image.open(image).convert("RGB"))
    keyed = background_to_alpha(rgb, tolerance)
    mask = keyed[..., 3] > 0

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


def _shoulder_row(band: np.ndarray, height: int) -> tuple[int, bool]:
    """Rows from the top to the head-to-shoulder step, and whether it was guessed."""
    span = max(3, height // 40)
    smooth = np.convolve(band, np.ones(span) / span, mode="same")
    search_lo, search_hi = int(height * 0.08), int(height * 0.40)
    slope = np.diff(smooth[:max(search_hi + 1, search_lo + 2)])
    rel = None
    if len(slope) > search_lo + 1:
        rise = slope[search_lo:]
        if len(rise):
            rel = search_lo + int(np.argmax(rise)) + 1
    if rel is None or rel >= height:
        return int(height * 0.20), True
    return rel, False


class _Figure:
    """A masked subject, and the coordinate reads a humanoid fit is made of."""

    def __init__(self, mask: np.ndarray, top: int, bottom: int,
                 centres: np.ndarray):
        self.mask = mask
        self.h, self.w = mask.shape
        self.top, self.bottom = top, bottom
        self.height = bottom - top
        self.centres = centres

    def px(self, y: int, x: float) -> list[float]:
        """A pixel position as a fraction of the canvas."""
        return [float(x) / self.w, float(y) / self.h]

    def centre_at(self, y: int) -> float:
        value = self.centres[max(self.top, min(self.bottom, y))]
        return float(value) if not math.isnan(value) else self.w / 2

    def split_centres(self, y: int) -> tuple[float, float]:
        """Left and right limb centres on one row, from the widest gap in it."""
        xs = np.nonzero(self.mask[max(self.top, min(self.bottom, y))])[0]
        if len(xs) == 0:
            c = self.centre_at(y)
            return c - self.w * 0.02, c + self.w * 0.02
        gaps = np.nonzero(np.diff(xs) > 1)[0]
        if len(gaps):
            cut = gaps[len(gaps) // 2]
            left, right = xs[: cut + 1], xs[cut + 1:]
            return float(left.mean()), float(right.mean())
        c = float(xs.mean())
        quarter = (xs.max() - xs.min()) / 4 or self.w * 0.02
        return c - quarter, c + quarter

    def confidence(self, band: np.ndarray, ankles: tuple[float, float], *,
                   guessed_shoulder: bool) -> tuple[float, bool]:
        """How much to trust the fit, and whether the legs read as one mass."""
        signals = [min(1.0, self.height / (self.h * 0.45)),
                   0.35 if guessed_shoulder else 1.0]
        waist = band[int(self.height * 0.30):int(self.height * 0.45)]
        if len(waist) and band.max() > 0:
            signals.append(min(1.0, (1.0 - waist.min() / band.max()) * 2.2))
        one_mass = abs(ankles[1] - ankles[0]) <= self.w * 0.01
        signals.append(0.2 if one_mass else 1.0)
        return float(np.mean(signals)), one_mass

    def rows(self, band: np.ndarray) -> tuple[dict[str, int], bool]:
        """Which row each landmark sits on, and whether the shoulder was guessed."""
        shoulder_rel, guessed = _shoulder_row(band, self.height)
        shoulder_y = self.top + shoulder_rel

        lower_start, lower_end = int(self.height * 0.42), int(self.height * 0.72)
        mid = band[lower_start:lower_end]
        hip_y = self.top + lower_start + (
            int(np.argmax(mid)) if len(mid) else int(self.height * 0.15))
        ankle_y = self.bottom - max(1, int(self.height * 0.03))

        torso = max(hip_y - shoulder_y, int(self.height * 0.15))
        return {
            "nose": self.top + int(self.height * 0.11),
            "shoulder": shoulder_y,
            "hip": hip_y,
            "ankle": ankle_y,
            "knee": int((hip_y + ankle_y) / 2),
            "elbow": min(self.bottom, shoulder_y + int(torso * 0.55)),
            "wrist": min(self.bottom, shoulder_y + int(torso * 1.05)),
        }, guessed

    def points(self, y: dict[str, int]
               ) -> tuple[dict[str, list[float]], float, float]:
        """The fourteen joints placed across each row, plus the two ankle centres."""
        w, px, centre_at = self.w, self.px, self.centre_at
        knee_l, knee_r = self.split_centres(y["knee"])
        ankle_l, ankle_r = self.split_centres(y["ankle"])

        shoulder_span = _extent(self.mask, y["shoulder"])
        hip_span = _extent(self.mask, y["hip"])
        elbow_span = _extent(self.mask, y["elbow"])
        wrist_span = _extent(self.mask, y["wrist"])

        centre_x = centre_at(y["shoulder"])
        half = ((shoulder_span[1] - shoulder_span[0]) / 2 if shoulder_span
                else w * 0.08)
        hip_half = (hip_span[1] - hip_span[0]) / 2 if hip_span else half * 0.7

        return {
            "nose": px(y["nose"], centre_at(y["nose"])),
            "neck": px(y["shoulder"] - int(self.height * 0.02), centre_x),
            "l_shoulder": px(y["shoulder"], centre_x + half * 0.78),
            "r_shoulder": px(y["shoulder"], centre_x - half * 0.78),
            "l_elbow": px(y["elbow"], (elbow_span[1] if elbow_span else centre_x + half) - w * 0.01),
            "r_elbow": px(y["elbow"], (elbow_span[0] if elbow_span else centre_x - half) + w * 0.01),
            "l_wrist": px(y["wrist"], (wrist_span[1] if wrist_span else centre_x + half) - w * 0.01),
            "r_wrist": px(y["wrist"], (wrist_span[0] if wrist_span else centre_x - half) + w * 0.01),
            "l_hip": px(y["hip"], centre_at(y["hip"]) + hip_half * 0.5),
            "r_hip": px(y["hip"], centre_at(y["hip"]) - hip_half * 0.5),
            "l_knee": px(y["knee"], knee_r),
            "r_knee": px(y["knee"], knee_l),
            "l_ankle": px(y["ankle"], ankle_r),
            "r_ankle": px(y["ankle"], ankle_l),
        }, ankle_l, ankle_r


def fit_humanoid(mask: np.ndarray, rig=None) -> Fit:
    """Read a standing humanoid's joints off its width profile."""
    rig = rig or rigs.HUMANOID
    fit = Fit()

    rows = np.nonzero(mask.any(axis=1))[0]
    if len(rows) < 12:
        fit.notes.append("subject too small to fit")
        return fit
    top, bottom = int(rows.min()), int(rows.max())
    if bottom - top < 12:
        fit.notes.append("subject too short to fit")
        return fit

    widths, centres = _profile(mask)
    band = widths[top:bottom + 1]
    if band.max() <= 0:
        return fit

    figure = _Figure(mask, top, bottom, centres)
    landmarks, guessed_shoulder = figure.rows(band)
    if guessed_shoulder:
        fit.notes.append("no clear head-to-shoulder step; used a default height")

    fit.points, ankle_l, ankle_r = figure.points(landmarks)
    fit.confidence, one_mass = figure.confidence(
        band, (ankle_l, ankle_r), guessed_shoulder=guessed_shoulder)
    if one_mass:
        fit.notes.append("legs did not separate; the fit may be a single mass")

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
        group = rigs.group_of(a, b)
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
