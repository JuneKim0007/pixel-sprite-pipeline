

from __future__ import annotations
from ..shared import paths

import json
from dataclasses import dataclass, field
from pathlib import Path
from ..shared.errors import Internal, Invalid
from ..shared.registry import Broken, Registry, Scanned


@dataclass
class Palette:
    key: str                        # "retro/pico8"
    group: str
    name: str
    path: Path
    colours: list[tuple[int, int, int]]
    tags: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def size(self) -> int:
        return len(self.colours)

    def summary(self) -> dict:
        return {
            "key": self.key, "group": self.group, "name": self.name,
            "size": self.size, "tags": self.tags,
            "description": self.description,
            "swatch": ["#%02X%02X%02X" % c for c in self.colours[:8]],
        }


_REGISTRIES: dict[Path, Registry] = {}


def _parse(path: Path, root: Path) -> Palette:
    colours: list[tuple[int, int, int]] = []
    meta: dict[str, str] = {}
    notes: list[str] = []

    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("//"):
            body = line[2:].strip()
            if ":" in body:
                k, v = body.split(":", 1)
                if k.strip().lower() in {"name", "tags"}:
                    meta[k.strip().lower()] = v.strip()
                    continue
            notes.append(body)
            continue
        token = line.lstrip("#").split()[0][:6]
        if len(token) == 6:
            try:
                colours.append(
                    (int(token[0:2], 16), int(token[2:4], 16), int(token[4:6], 16))
                )
            except ValueError:
                pass

    rel = path.relative_to(paths.resolve(root, "palettes"))
    group = rel.parent.name if rel.parent != Path(".") else "general"
    key = f"{group}/{path.stem}" if group != "general" else path.stem
    return Palette(
        key=key,
        group=group,
        name=meta.get("name", path.stem.replace("_", " ").title()),
        path=path,
        colours=colours,
        tags=[t.strip() for t in meta.get("tags", "").split(",") if t.strip()],
        description=" ".join(notes),
    )


def registry(root: Path) -> Registry[Palette]:
    """The palettes under `root`, cached until one of the files changes.

    One registry per root, kept because the scan used to run on every call and
    a palette list is asked for on nearly every page load.
    """
    root = Path(root).resolve()
    found = _REGISTRIES.get(root)
    if found is None:
        found = Registry("palette", Scanned(
            paths.resolve(root, "palettes"), ["**/*.hex"],
            lambda path: _entry(path, root), what="palette"))
        _REGISTRIES[root] = found
    return found


def _entry(path: Path, root: Path) -> tuple[str, Palette]:
    """Parse one file, and refuse it out loud rather than by omission.

    A palette with no colours in it used to be dropped silently, so a typo
    presented as "the file I just wrote is not in the list" with nothing
    anywhere saying why.
    """
    palette = _parse(path, root)
    if not palette.colours:
        raise Invalid("no colours found in this file",
                      hint="a palette is one six-digit hex value per line")
    return palette.key, palette


def discover(root: Path) -> dict[str, Palette]:
    return registry(root).all()


def broken(root: Path) -> list[Broken]:
    """Files that look like palettes and would not load."""
    return registry(root).broken()


def grouped(root: Path) -> dict[str, list[Palette]]:
    out: dict[str, list[Palette]] = {}
    for pal in discover(root).values():
        out.setdefault(pal.group, []).append(pal)
    return out


CHOOSE_SYSTEM = """You choose a colour palette for pixel art. Output ONLY JSON.
Judge by mood and subject matter, not by palette size. Prefer a palette whose
tags match the subject."""

CHOOSE_TEMPLATE = """Subject: {subject}
Style: {style}

Available palettes:
{catalogue}

Reply exactly: {{"key": "<one key from the list>", "why": "<short reason>"}}"""


def choose(
    root: Path, subject: str, style: str, client, *, temperature: float = 0.4,
    attempts: int = 3, verbose: bool = True,
) -> Palette:
    """Ask the LLM to pick a palette; verify the pick exists."""
    available = discover(root)
    if not available:
        # Nothing the caller sent is at fault here - subject/style/client are
        # all fine. An empty palettes/ is a broken install, so this is ours
        # to fix, not theirs.
        raise Internal("no palettes found under palettes/",
                       hint="add a .hex file under palettes/")

    catalogue = "\n".join(
        f"- {p.key}: {p.name} ({p.size} colours)"
        + (f" tags: {', '.join(p.tags)}" if p.tags else "")
        + (f" — {p.description}" if p.description else "")
        for p in available.values()
    )
    prompt = CHOOSE_TEMPLATE.format(
        subject=subject, style=style, catalogue=catalogue
    )

    for attempt in range(1, attempts + 1):
        raw = client.generate(
            prompt, system=CHOOSE_SYSTEM, temperature=temperature, json_mode=True
        )
        try:
            picked = json.loads(raw).get("key", "").strip()
        except json.JSONDecodeError:
            picked = ""

        if picked in available:
            if verbose:
                print(f"   palette chosen: {picked} ({available[picked].name})")
            return available[picked]

        if verbose:
            print(f"   attempt {attempt}: invalid pick {picked!r}")
        prompt += (
            f"\n\n'{picked}' is not in the list. Reply with one of exactly these "
            f"keys: {', '.join(available)}"
        )

    fallback = next(iter(available.values()))
    if verbose:
        print(f"   LLM failed to choose; falling back to {fallback.key}")
    return fallback
