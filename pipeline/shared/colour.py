"""Backdrop colours, shared by the prompt that asks for one and the keyer that removes it."""

from __future__ import annotations

import re

BACKDROP = "#FF00FF"

BACKDROP_PRESETS: tuple[tuple[str, str], ...] = (
    ("#FF00FF", "Magenta, furthest from skin and cloth"),
    ("#00B140", "Chroma green, bleeds into skin tones"),
    ("#0047BB", "Chroma blue, for green or yellow subjects"),
    ("#7F7F7F", "Neutral grey, for saturated subjects"),
)

# What to call a backdrop in a prompt.
COLOUR_NAMES: tuple[tuple[tuple[int, int, int], str], ...] = (
    ((255, 0, 255), "magenta"),
    ((242, 94, 147), "hot pink"),
    ((0, 177, 64), "bright green"),
    ((0, 71, 187), "deep blue"),
    ((127, 127, 127), "mid grey"),
    ((255, 0, 0), "red"),
    ((255, 255, 0), "yellow"),
    ((0, 255, 255), "cyan"),
    ((255, 255, 255), "white"),
    ((0, 0, 0), "black"),
)


def name_for(raw) -> str:
    """The nearest colour a caption would use, for a prompt CLIP can read."""
    rgb = parse_colour(raw) or parse_colour(BACKDROP)
    return min(COLOUR_NAMES,
               key=lambda entry: sum((a - b) ** 2 for a, b in zip(entry[0], rgb)))[1]

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB = re.compile(r"^(?:rgb\s*\(\s*)?(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*\)?$")


def parse_colour(raw) -> tuple[int, int, int] | None:
    """A backdrop colour as RGB or hex, in the forms someone actually types."""
    from .errors import Invalid

    text = str(raw or "").strip()
    if not text:
        return None

    hex_match = _HEX.match(text)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) == 3:
            digits = "".join(d * 2 for d in digits)
        return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))

    rgb_match = _RGB.match(text)
    if rgb_match:
        channels = tuple(int(g) for g in rgb_match.groups())
        if all(0 <= c <= 255 for c in channels):
            return channels
        raise Invalid(f"'{text}' has a channel outside 0-255", field="colour")

    raise Invalid(f"'{text}' is not a colour", field="colour",
                  hint="RGB as '12, 34, 56' or hex as '#0a1b2c'.")
