
from __future__ import annotations

import os
from pathlib import Path

import ruamel.yaml

from ..shared import settings

ROOT = Path(__file__).resolve().parent.parent.parent
STATIC = ROOT / "web"
CONFIGS = ROOT / "configs"

_RT = ruamel.yaml.YAML()
_RT.preserve_quotes = True
_RT.width = 4096


def load_roundtrip(path: Path):
    with path.open() as fh:
        return _RT.load(fh)


def dump_roundtrip(data, path: Path) -> None:

    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with tmp.open("w") as fh:
            _RT.dump(data, fh)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def global_cfg() -> dict:
    return settings.load_global(ROOT)


def runs_dir() -> Path:
    return settings.resolve_dir(
        ROOT, (global_cfg().get("paths") or {}).get("output_dir"), "out/runs"
    )


def input_dir() -> Path:
    return settings.resolve_dir(
        ROOT, (global_cfg().get("paths") or {}).get("input_dir"), "inputs"
    )


def download_dir() -> Path:
    return settings.resolve_dir(
        ROOT, (global_cfg().get("paths") or {}).get("download_dir"), "exports"
    )


def allowed_roots() -> list[Path]:
    """Directories the browser is permitted to read or write.

    The configured input/output/download directories may sit outside the
    project, so they are listed explicitly rather than assuming everything
    lives under ROOT.
    """
    return [ROOT, input_dir(), runs_dir(), download_dir(), Path.home()]


def human_size(n: int) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < step:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} PB"


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
