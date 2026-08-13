from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import stages  # noqa: E402, F401  (importing registers them)


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture
def outdir(tmp_path: Path) -> Path:
    return tmp_path
