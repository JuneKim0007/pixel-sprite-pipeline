"""Style sheets: a reusable, composable description of a look.

Before this, an aesthetic decision had nowhere to live. "Pokémon-like,
monochrome, dynamic poses" was retyped into `subject` and `style` for every
pipeline, and drifted between them — while the settings that also carry a look
(palette, LoRA strength, CFG) sat somewhere else entirely.

A style sheet is deliberately not restricted to prompts. It may set any config
subtree, because consistency is the goal and a tidy boundary that forces you to
retype `lora_strength` in three files does not serve it. It slots in as a
layer:

    global defaults  ->  style sheets  ->  pipeline config  ->  job overrides

so a pipeline still wins over a style, and a single job still wins over
everything.

Style survives across four mechanisms of increasing strength, and a sheet can
carry all of them at once — the file's shape does not change as a style matures
from words into trained weights:

    vocabulary      prompt fragments. Instant, weak.
    settings        palette and sampler values. Exact, because palette snapping
                    is deterministic — frames cannot drift in colour.
    exemplars       images fed to IP-Adapter alongside the character reference.
                    Strong, no training.
    token / lora    a textual-inversion pseudo-word, then a style LoRA. The
                    strongest, and the only ones that need training.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .settings import deep_merge

DIRNAME = "styles"
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class StyleError(RuntimeError):
    pass


@dataclass
class Style:
    name: str
    label: str
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def vocabulary(self) -> dict[str, list[str]]:
        raw = self.data.get("vocabulary") or {}
        return {k: list(v) for k, v in raw.items() if isinstance(v, list)}

    @property
    def exemplars(self) -> list[str]:
        return list((self.data.get("references") or {}).get("exemplars") or [])

    @property
    def token(self) -> str:
        return str(self.data.get("token") or "")

    @property
    def lora(self) -> dict[str, Any]:
        return dict(self.data.get("lora") or {})

    def summary(self, root: Path) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "extends": list(self.data.get("extends") or []),
            "vocabulary": self.vocabulary,
            "exemplars": [str(root / p) for p in self.exemplars],
            "token": self.token,
            "lora": self.lora,
            "palette": (self.data.get("settings") or {}).get("palette", {}).get("file"),
            "modules": sorted((self.data.get("modules") or {})),
            "notes": self.data.get("notes", ""),
        }


def styles_dir(root: Path) -> Path:
    path = root / DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def discover(root: Path) -> dict[str, Style]:
    found: dict[str, Style] = {}
    for f in sorted(styles_dir(root).glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text()) or {}
        except yaml.YAMLError:
            continue
        name = data.get("name", f.stem)
        found[name] = Style(
            name=name, label=data.get("label", name), path=f, data=data
        )
    return found


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
        if name not in available:
            raise StyleError(
                f"no style '{name}'. Available: "
                f"{', '.join(sorted(available)) or '(none)'}"
            )
        seen.add(name)
        style = available[name]
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
        exemplars += [str((root / p)) for p in style.exemplars]
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
