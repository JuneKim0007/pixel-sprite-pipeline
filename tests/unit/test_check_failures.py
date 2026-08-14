"""The checker that keeps user-reachable failures named.

Exercised through its public functions on fixture trees, never by reading its
own source: a checker that only works on this repo's current shape is not a
checker, it is a snapshot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import check_failures as cf


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


def test_a_function_called_by_a_handler_is_reachable(tmp_path):
    _tree(tmp_path, {"api.py": """
from .routing import get

class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        return load()

def load():
    return helper()

def helper():
    return 1

def unrelated():
    return 2
"""})
    index = cf.Index([tmp_path])
    found = {name for _, name in cf.reachable(index, cf.entry_points(index))}
    assert "listing" in found
    assert "load" in found
    assert "helper" in found, "reachability stopped at one hop"
    assert "unrelated" not in found, "everything was treated as reachable"
