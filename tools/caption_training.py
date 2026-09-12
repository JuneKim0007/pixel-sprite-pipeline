#!/usr/bin/env python3
"""Write one caption per training image, so content is named and style is not.
What a caption omits is absorbed into the trigger, per pipeline/looks/training.py."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.looks import training  # noqa: E402
from pipeline.refs.llm import LLMError, Ollama  # noqa: E402

SYSTEM = (
    "You caption images for training a style model. You describe WHAT is "
    "shown, never HOW it is drawn."
)

TEMPLATE = """Describe this character in one line, under fifteen words.

Name: the subject, what they wear or carry, the pose, and the view
(front, side, three-quarter, rear).

Never name the art style, the medium, the palette, the resolution or the
rendering. Do not write "pixel art", "sprite", "8-bit", "chibi", "anime",
"flat colours" or anything like them.

Reply with the line only. No quotes, no preamble."""

# Words that describe the drawing rather than the thing drawn; naming one hands the style back.
STYLE_WORDS = (
    "pixel", "sprite", "8-bit", "8 bit", "16-bit", "16 bit", "anime", "chibi",
    "cartoon", "illustration", "render", "digital art", "artwork", "drawing",
    "flat colour", "flat color", "dithered", "palette", "low-res", "lowres",
    "retro", "game art", "vector", "painting",
)


def scrub(line: str) -> str:
    """Excise the style words, keep the subject around them.
    Dropping the whole clause would lose "a witch in a wide hat" to its first two words."""
    import re

    text = line.strip().strip(".")
    for word in STYLE_WORDS:
        # "pixel art of a witch" -> "a witch"; "a 16-bit sprite" -> "a"
        text = re.sub(rf"\b{re.escape(word)}\b(\s+(art|style|of))*", " ",
                      text, flags=re.I)
    parts = []
    for part in re.split(r"[,;]", text):
        piece = re.sub(r"\s+", " ", part).strip(" .-")
        piece = re.sub(r"^(of|a|an|the|in|and)\b\s*", "", piece, flags=re.I).strip()
        if len(piece.split()) >= 2:
            parts.append(piece)
    return ", ".join(parts)


def caption_one(client: Ollama, image: Path, trigger: str) -> str:
    raw = client.look(TEMPLATE, [image], system=SYSTEM).strip()
    body = scrub(raw.splitlines()[0] if raw else "")
    if not body:
        raise LLMError(f"{image.name}: nothing left after removing style words "
                       f"from {raw!r}")
    return f"{trigger}, {body}" if trigger else body


def main() -> int:
    import argparse

    style = training.target("style")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=ROOT / "training_set/sprite")
    ap.add_argument("--trigger", default="",
                    help="a word that means this style, prefixed to every line")
    ap.add_argument("--model", default="qwen2.5vl:3b", help="must be a VLM")
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()

    print(f"rule: {style.caption}\n")

    client = Ollama(host=a.host, model=a.model, keep_alive=0)
    if not client.alive():
        raise SystemExit(f"Ollama is not answering at {a.host}. Start it with "
                         f"`ollama serve`, then `ollama pull {a.model}`.")

    images = sorted(p for p in a.src.glob("*.png"))
    if not images:
        raise SystemExit(f"no images under {a.src}")

    written = 0
    for image in images:
        beside = image.with_suffix(".txt")
        if beside.exists() and not a.overwrite:
            print(f"  keep  {image.name}  {beside.read_text().strip()[:60]}")
            continue
        try:
            line = caption_one(client, image, a.trigger)
        except LLMError as err:
            print(f"  SKIP  {image.name}: {err}")
            continue
        beside.write_text(line + "\n")
        written += 1
        print(f"  wrote {image.name}  {line}")

    print(f"\n{written} caption(s) written to {a.src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
