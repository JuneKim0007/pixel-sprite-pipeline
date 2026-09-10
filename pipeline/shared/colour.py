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
