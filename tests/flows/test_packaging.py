from __future__ import annotations

import ast
import importlib
import pathlib
import pkgutil

import pytest

import pipeline

GROUPS = {"geometry", "refs", "looks", "generation", "orchestration",
          "definitive", "api", "stages"}
MODULES = sorted(info.name for info in pkgutil.walk_packages(pipeline.__path__,
                                                             "pipeline.")
                 if "__pycache__" not in info.name)
SHARED = sorted((pathlib.Path(pipeline.__path__[0]) / "shared").rglob("*.py"))


@pytest.mark.parametrize("name", MODULES)
def test_every_module_imports(name):
    # A broken import is invisible until something imports that module, which for half of them is only when a particular route is called.
    importlib.import_module(name)


@pytest.mark.parametrize("path", SHARED, ids=lambda p: p.name)
def test_shared_depends_on_no_module(path):
    leaks = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.ImportFrom):
            continue
        head = (node.module or "").split(".")[0]
        if node.level and head in GROUPS:
            leaks.append(node.module)
        if not node.level and node.module and node.module.startswith("pipeline."):
            leaks.append(node.module)
    assert not leaks, f"shared/ reaches into {leaks}"
