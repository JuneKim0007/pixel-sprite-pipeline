
from __future__ import annotations


import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..shared.settings import deep_merge
from ..shared.errors import Invalid
from ..shared.registry import Registry, Scanned

DIRNAME = "styles"
SHEET = "style.yaml"
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
IMAGES = (".png", ".jpg", ".jpeg", ".webp")

_REGISTRIES: dict[Path, "Registry[Style]"] = {}


class StyleError(Invalid):
    """A style sheet contradicts itself: a cycle, or two sheets one name."""


@dataclass
class Style:
    name: str
    label: str
    path: Path                                    # the YAML document itself
    home: Path                                    # what relative paths mean
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def foldered(self) -> bool:
        return self.path.name == SHEET

    def resolve(self, relative: str) -> Path:
        return (self.home / relative).resolve()

    @property
    def vocabulary(self) -> dict[str, list[str]]:
        raw = self.data.get("vocabulary") or {}
        return {k: list(v) for k, v in raw.items() if isinstance(v, list)}

    @property
    def exemplars(self) -> list[Path]:
        """Listed exemplars, then whatever was dropped into context/exemplars.

        Auto-discovery is the point of the directory form. Adding a reference
        image should be a file copy, not a file copy plus a YAML edit, because
        the YAML edit is the step people skip and then wonder why the look did
        not change.
        """
        out: list[Path] = []
        for rel in (self.data.get("references") or {}).get("exemplars") or []:
            out.append(self.resolve(str(rel)))

        for found in sorted((self.home / "context" / "exemplars").glob("*")):
            if found.suffix.lower() in IMAGES and found not in out:
                out.append(found)
        return out

    @property
    def notes(self) -> str:
        """Prose about the look. The sheet's own `notes:`, then context/notes.md."""
        written = str(self.data.get("notes", "") or "")
        sidecar = self.home / "context" / "notes.md"
        if sidecar.exists():
            extra = sidecar.read_text().strip()
            return f"{written}\n\n{extra}".strip() if written else extra
        return written

    @property
    def token(self) -> str:
        return str(self.data.get("token") or "")

    @property
    def lora(self) -> dict[str, Any]:
        return dict(self.data.get("lora") or {})

    @property
    def tuning(self) -> dict[str, Any]:
        """Which settings this look permits a tuner to move, and how far."""
        return dict(self.data.get("tuning") or {})

    @property
    def training_images(self) -> list[Path]:
        """Outputs promoted as future LoRA data. Accumulates; never read at run time."""
        folder = self.home / "training" / "images"
        return sorted(p for p in folder.glob("*") if p.suffix.lower() in IMAGES)

    def summary(self, root: Path) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "extends": list(self.data.get("extends") or []),
            "vocabulary": self.vocabulary,
            "exemplars": [str(p) for p in self.exemplars],
            "token": self.token,
            "lora": self.lora,
            "palette": (self.data.get("settings") or {}).get("palette", {}).get("file"),
            "modules": sorted((self.data.get("modules") or {})),
            "notes": self.notes,
            "foldered": self.foldered,
            "home": str(self.home),
            "tuning": self.tuning,
            "training_images": len(self.training_images),
        }


def styles_dir(root: Path) -> Path:
    path = root / DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path, home: Path) -> tuple[str, Style]:
    """One sheet. A YAML error names the file now instead of hiding it.

    A sheet that will not parse used to be skipped silently, which presents as
    the style disappearing from the list - the same failure a malformed palette
    had, and the same fix.
    """
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise Invalid(str(e).split("\n")[0]) from e
    if not isinstance(data, dict):
        raise Invalid("a style sheet should be a mapping")

    # A foldered sheet is named by its folder unless it says otherwise, so
    # renaming the directory renames the style.
    default = home.name if path.name == SHEET else path.stem
    name = data.get("name", default)
    return name, Style(name=name, label=data.get("label", name),
                       path=path, home=home, data=data)


def registry(root: Path) -> Registry[Style]:
    """Every sheet, in both layouts. A name may only be claimed once."""
    root = Path(root).resolve()
    found = _REGISTRIES.get(root)
    if found is None:
        styles_dir(root)
        found = Registry("style sheet", Scanned(
            root / DIRNAME, ["*.yaml", f"*/{SHEET}"],
            lambda path: _read(path, path.parent if path.name == SHEET else root),
            what="style sheet"))
        _REGISTRIES[root] = found
    return found


def discover(root: Path) -> dict[str, Style]:
    return registry(root).all()


def broken(root: Path) -> list:
    """Sheets that would not load, so the UI can say which and why."""
    return registry(root).broken()


def _chain(root: Path, names: list[str], seen: set[str] | None = None) -> list[Style]:
    """Flatten `extends` into an application order, bases first.

    Depth-first so a sheet's own values land after everything it inherits, and
    cycle-guarded because a style importing itself should be an error message
    rather than a hang.
    """
    seen = seen if seen is not None else set()
    available = discover(root)
    out: list[Style] = []

    for name in names:
        if name in seen:
            raise StyleError(f"style '{name}' is part of an extends cycle")
        seen.add(name)
        style = registry(root).get(name)
        out += _chain(root, list(style.data.get("extends") or []), seen)
        out.append(style)
    return out


def resolve_vocabulary(styles: list[Style], picks: dict[str, Any] | None = None) -> dict[str, str]:
    """Collapse each vocabulary group into one comma-joined phrase.

    `picks` narrows a group to a chosen subset, which is what the wizard's
    style chips toggle for a single run without editing the sheet.
    """
    merged: dict[str, list[str]] = {}
    for style in styles:
        for group, fragments in style.vocabulary.items():
            merged.setdefault(group, [])
            for fragment in fragments:
                if fragment not in merged[group]:
                    merged[group].append(fragment)

    chosen = picks or {}
    out: dict[str, str] = {}
    for group, fragments in merged.items():
        keep = chosen.get(group)
        if isinstance(keep, list):
            fragments = [f for f in fragments if f in keep]
        elif isinstance(keep, str):
            fragments = [keep]
        out[group] = ", ".join(fragments)
    return out


def expand(text: Any, vocabulary: dict[str, str]) -> Any:
    """Substitute {group} placeholders, dropping ones with nothing to say.

    An unresolved placeholder is left alone rather than erased, so a typo shows
    up in the prompt preview instead of silently vanishing.
    """
    if isinstance(text, dict):
        return {k: expand(v, vocabulary) for k, v in text.items()}
    if isinstance(text, list):
        return [expand(v, vocabulary) for v in text]
    if not isinstance(text, str):
        return text

    def swap(match: re.Match) -> str:
        return vocabulary.get(match.group(1), match.group(0))

    filled = PLACEHOLDER.sub(swap, text)
    # Collapse the gaps an empty group leaves behind.
    filled = re.sub(r",\s*,", ",", filled)
    return filled.strip().strip(",").strip()


def layer(
    root: Path,
    config: dict[str, Any],
    *,
    picks: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply the config's `styles:` list beneath it.

    Returns (config_with_styles_applied, record). The record is what the UI
    shows: which sheets were used, the resolved vocabulary, and the exemplars
    now in play.
    """
    names = list(config.get("styles") or [])
    if not names:
        return config, {"styles": [], "vocabulary": {}, "exemplars": []}

    chain = _chain(root, names)
    module = config.get("module", "animation")
    vocabulary = resolve_vocabulary(chain, picks)

    base: dict[str, Any] = {}
    exemplars: list[str] = []
    tokens: list[str] = []

    for style in chain:
        base = deep_merge(base, expand(style.data.get("settings") or {}, vocabulary))
        per_module = (style.data.get("modules") or {}).get(module) or {}
        base = deep_merge(base, expand(per_module, vocabulary))
        exemplars += [str(p) for p in style.exemplars]
        if style.token:
            tokens.append(style.token)

    # A trained token is just another prompt fragment, so a style that has
    # graduated from words to an embedding needs no different handling.
    if tokens:
        vocabulary["token"] = " ".join(tokens)
        for key in ("style", "subject"):
            if key in base and isinstance(base[key], str):
                base[key] = f"{' '.join(tokens)}, {base[key]}"

    merged = deep_merge(base, config)

    # Style exemplars ride alongside the character references rather than
    # replacing them: one anchors the look, the other the identity.
    if exemplars:
        refs = dict(merged.get("references") or {})
        refs["style_exemplars"] = exemplars
        merged["references"] = refs

    return merged, {
        "styles": [s.name for s in chain],
        "vocabulary": vocabulary,
        "exemplars": exemplars,
        "tokens": tokens,
    }


def preview(root: Path, config: dict[str, Any], picks: dict[str, Any] | None = None) -> dict:
    """What the pipeline would actually send, without running anything."""
    merged, record = layer(root, config, picks=picks)
    module = config.get("module", "animation")

    subject = merged.get("subject", "")
    style_text = merged.get("style", "")
    stage_prompt = (merged.get("frames") or {}).get("prompt")

    conflicts = []
    for style_name in record["styles"]:
        sheet = discover(root).get(style_name)
        if not sheet:
            continue
        for key in (sheet.data.get("settings") or {}):
            if key in config:
                conflicts.append(f"{style_name} sets '{key}', which this pipeline also pins")

    return {
        "module": module,
        "record": record,
        "resolved_prompt": stage_prompt or ", ".join(p for p in (subject, style_text) if p),
        "conflicts": conflicts,
        "palette": (merged.get("palette") or {}).get("file"),
    }
