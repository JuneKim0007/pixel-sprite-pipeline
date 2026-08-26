from __future__ import annotations

DEFAULT_SUBJECT = "a knight in armor"
DEFAULT_STYLE = "pixel art, game sprite, side view, plain flat background"

NEGATIVE = (
    "blurry, soft, smooth gradient, antialiased, jpeg artifacts, photo, "
    "realistic, 3d render, watermark, signature, text, extra limbs, "
    "deformed, low contrast, muddy colors"
)

POSE_NEGATIVE = (
    "skeleton, skull, bones, bony, undead, lich, ribcage, x-ray, anatomical "
    "diagram, stick figure, wireframe, rainbow limbs, mannequin"
)

BACKDROP = "#FF00FF"
BACKDROP_TERMS = (
    "solid flat {colour} chroma key background, uniform background colour, "
    "no shadow, no gradient, no ground plane"
)
BACKDROP_NEGATIVE = (
    "cast shadow, drop shadow, ground shadow, floor, ground plane, vignette, "
    "background gradient, studio lighting, environment, scenery, backdrop "
    "texture"
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


def backdrop_prompt(colour: str | None) -> str:
    return BACKDROP_TERMS.format(colour=colour or BACKDROP)


def negative_for(base: str, *, backdrop: bool = False, pose_control: bool = False,
                 facing: str = "", guard_skeletons: bool = True,
                 guard_faces: bool = True) -> str:
    parts = [base]
    if backdrop:
        parts.append(BACKDROP_NEGATIVE)
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
