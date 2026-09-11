"""Turning a named config into a run directory, once, for three callers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..shared import paths, settings
from ..shared.errors import Invalid
from . import admission

SNAPSHOT = "config.yaml"


@dataclass(frozen=True)
class Prepared:
    """A run that is ready to start, and the directory it will fill."""

    cfg: dict[str, Any]
    outdir: Path
    run_id: str
    config_path: Path
    styles: list[str]


def runs_base(root: Path, cfg: dict) -> Path:
    """Where runs go, read from the config that will run - not from _global."""
    return paths.from_config(root, cfg, "output_dir")


def effective(root: Path, config_path: Path, overrides: dict | None = None,
              style_picks: dict | None = None) -> tuple[dict, dict, dict]:
    """The raw config as a run will see it: overrides applied, styles layered."""
    from ..generation import schema
    from ..looks import styles as styles_mod

    raw = settings.read_yaml(config_path)
    schema.apply_overrides(raw, overrides)
    if style_picks:
        raw["style_picks"] = style_picks
    cfg, record = styles_mod.effective(root, raw, picks=raw.get("style_picks"))
    return raw, cfg, record


def prepare(root: Path, config_path: Path, *, overrides: dict | None = None,
            style_picks: dict | None = None, run_id: str | None = None,
            name: str | None = None, base: Path | None = None) -> Prepared:
    """Refuse what cannot run, then make the directory it would have run in."""
    raw, cfg, record = effective(root, config_path, overrides, style_picks)

    refused = admission.problems(root, cfg)
    if refused:
        raise Invalid(refused[0], hint="; ".join(refused[1:]))

    rid = run_id or "{}_{}".format(
        time.strftime("%Y%m%d_%H%M%S"),
        name or cfg.get("name") or config_path.stem)
    outdir = (base or runs_base(root, cfg)) / rid
    outdir.mkdir(parents=True, exist_ok=True)

    # The snapshot is the RAW config: resume layers styles over it again, and a style.
    snapshot = outdir / SNAPSHOT
    if config_path.resolve() != snapshot.resolve():
        snapshot.write_text(yaml.safe_dump(raw, sort_keys=False))

    return Prepared(cfg=cfg, outdir=outdir, run_id=rid, config_path=snapshot,
                    styles=list(record.get("styles") or []))
