from __future__ import annotations

DEFAULT_SUBJECT = "a knight in armor"
DEFAULT_STYLE = "pixel art, game sprite, side view"

NEGATIVE = (
    "blurry, soft, smooth gradient, antialiased, jpeg artifacts, photo, "
    "realistic, 3d render, watermark, signature, text, extra limbs, "
    "deformed, low contrast, muddy colors"
)

POSE_NEGATIVE = (
    "skeleton, skull, bones, bony, undead, lich, ribcage, x-ray, anatomical "
    "diagram, stick figure, wireframe, rainbow limbs, mannequin"
)

from ..shared.colour import BACKDROP, BACKDROP_PRESETS  # noqa: F401
BACKDROP_TERMS = "solid flat {colour} chroma key background, uniform background color"
GROUND_NEGATIVE = (
    "cast shadow, drop shadow, ground shadow, floor, ground plane, "
    "studio lighting"
)
BACKDROP_NEGATIVE = (
    "vignette, background gradient, environment, scenery, backdrop texture"
)

VIEW_WORDS: tuple[tuple[float, str], ...] = (
    (22, "front view, facing the viewer"),
    (67, "three-quarter front view"),
    (112, "side view, profile"),
    (157, "three-quarter rear view"),
    (202, "rear view, seen from behind"),
    (247, "three-quarter rear view from the other side"),
    (292, "side view, profile facing the other way"),
    (337, "three-quarter front view from the other side"),
)
VIEW_WORDS_DEFAULT = "front view, facing the viewer"

FACE_NEGATIVE_REAR = "face, eyes, nose, mouth, facial features, front view"
FACE_NEGATIVE_NEAR_REAR = "both eyes visible, front-facing face"


def backdrop_colour(settings: dict | None) -> str | None:
    """The colour to key against, or None when the background is part of the art."""
    from ..shared.config import opt

    if opt(settings or {}, "enabled", True) is False:
        return None
    asked = opt(settings or {}, "colour", BACKDROP)
    # `auto` tells the KEYER to read the corners.
    return BACKDROP if str(asked).strip().lower() == "auto" else asked


def prompt_for(subject: str, hint: str, style: str, backdrop: str | None,
               held: str = "", style_emphasis: float = 1.0) -> str:
    """The positive prompt, from whichever terms a run actually has.

    `style_emphasis` wraps the style terms in CLIPTextEncode's (term:weight)
    attention syntax, so the look can outrank the subject beside it.
    """
    if style and abs(style_emphasis - 1.0) > 1e-3:
        style = f"({style}:{style_emphasis:.2f})"
    return ", ".join(p for p in (
        subject, hint, held, style,
        backdrop_prompt(backdrop) if backdrop else "") if p)


def backdrop_prompt(colour: str | None) -> str:
    from ..shared.colour import name_for

    return BACKDROP_TERMS.format(colour=name_for(colour or BACKDROP))


def negative_for(base: str, *, backdrop: bool = False, pose_control: bool = False,
                 facing: str = "", guard_skeletons: bool = True,
                 guard_faces: bool = True, guard_ground: bool = True) -> str:
    parts = [base]
    if backdrop:
        parts.append(BACKDROP_NEGATIVE)
        if guard_ground:
            parts.append(GROUND_NEGATIVE)
    if pose_control and guard_skeletons:
        parts.append(POSE_NEGATIVE)
    if facing and guard_faces:
        parts.append(facing)
    return ", ".join(p for p in parts if p)


def view_words(yaw: float) -> str:
    yaw %= 360
    for limit, words in VIEW_WORDS:
        if yaw < limit:
            return words
    return VIEW_WORDS_DEFAULT


def facing_negative(yaw: float) -> str:
    yaw %= 360
    if 135 <= yaw <= 225:
        return FACE_NEGATIVE_REAR
    if 100 < yaw < 135 or 225 < yaw < 260:
        return FACE_NEGATIVE_NEAR_REAR
    return ""


# Words that describe a backdrop.
BACKDROP_WORDS = ("plain background", "flat background", "white background",
                  "studio background", "backdrop", "background colour",
                  "background color")


def backdrop_conflict(style: str, backdrop: str | None) -> str:
    """A style phrase that argues with the backdrop being asked for, or ""."""
    if not backdrop:
        return ""
    said = (style or "").lower()
    for phrase in BACKDROP_WORDS:
        if phrase in said:
            return phrase
    return ""
