"""What each conditioning dial does, in words a non-technical reader can use."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dial:
    """One setting, as a person reading a banner needs it."""

    label: str
    plain: str
    more: str
    less: str


DIALS: dict[str, Dial] = {
    "canonical.lora_strength": Dial(
        label="Pixel style",
        plain="How hard the pixel-art style is pushed onto the drawing.",
        more="Chunkier blocks, and less like the picture you supplied. "
             "Above about 1.0 only the likeness moves.",
        less="Finer, closer to an illustration - but too low and it stops "
             "being either.",
    ),
    "canonical.from_reference.weight": Dial(
        label="Follow the reference",
        plain="How closely the result copies the picture you supplied.",
        more="Looks more like your character, and picks up the background "
             "and brushwork of that picture too.",
        less="More freedom for the model, less like your character.",
    ),
    "canonical.from_reference.weight_type": Dial(
        label="What to copy",
        plain="Which part of the reference to take: its colours, its shape, "
              "or everything.",
        more="'Everything' gives the strongest likeness.",
        less="'Colours only' keeps the pose you asked for; 'shape only' "
             "argues with it.",
    ),
    "canonical.from_reference.weight_composition": Dial(
        label="Follow its shape",
        plain="How much of the reference's body shape and layout to take, "
              "separately from its colours.",
        more="The build and proportions follow the reference.",
        less="The pose you set is left alone.",
    ),
    "canonical.controlnet.strength": Dial(
        label="Hold the pose",
        plain="How strictly the character sticks to the pose you set.",
        more="The pose is obeyed exactly, and can start to look stiff.",
        less="The model reinterprets the pose.",
    ),
    "canonical.controlnet.end_percent": Dial(
        label="How long to hold it",
        plain="How far into the drawing the pose keeps being enforced.",
        more="The pose survives to the end.",
        less="The pose is set early and then let go, which can leave a "
             "second pair of arms behind.",
    ),
    "canonical.depth_controlnet.strength": Dial(
        label="Hold the body",
        plain="How strictly the body's thickness and mass are kept.",
        more="The figure matches the body shape it was given.",
        less="The model decides the build.",
    ),
    "canonical.style_emphasis": Dial(
        label="Say it louder",
        plain="Repeats the style words more insistently in the prompt.",
        more="Nothing measurable - pictures outvote words here.",
        less="Normal.",
    ),
    "palette.size": Dial(
        label="Colours",
        plain="How many colours the finished sprite may use.",
        more="Smoother shading, less of a sprite look.",
        less="Flatter, more clustered colour.",
    ),
    "palette.factor": Dial(
        label="Block size",
        plain="How many screen pixels make up one pixel-art square.",
        more="Bigger, chunkier squares.",
        less="Finer squares, closer to a normal drawing.",
    ),
}


def rendered() -> dict[str, dict]:
    return {k: {"label": d.label, "plain": d.plain, "more": d.more,
                "less": d.less}
            for k, d in DIALS.items()}
