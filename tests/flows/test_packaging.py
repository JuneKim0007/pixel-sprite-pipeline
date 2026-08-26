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


def _group_imports() -> dict[str, dict[str, list[str]]]:
    """group -> group -> the import statements that make the edge."""
    base = pathlib.Path(pipeline.__path__[0])
    edges: dict[str, dict[str, list[str]]] = {}
    for path in sorted(base.rglob("*.py")):
        parts = path.relative_to(base).parts
        here = parts[0] if len(parts) > 1 else ""
        if here not in GROUPS:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                anchor = path.parent
                for _ in range(node.level - 1):
                    anchor = anchor.parent
                target = anchor / (node.module or "").replace(".", "/")
                try:
                    rel = target.resolve().relative_to(base.resolve())
                except ValueError:
                    continue
                there = rel.parts[0] if rel.parts else ""
            else:
                there = (node.module or "").split(".")[1:2]
                there = there[0] if there else ""
            if there in GROUPS and there != here:
                edges.setdefault(here, {}).setdefault(there, []).append(
                    f"{path.relative_to(base)}:{node.lineno}")
    return edges


def test_no_group_imports_form_a_cycle():
    # Three cycles once made the seven groups un-layerable: cooling sat in `orchestration` importing nothing, a pixel layer reached into the palette registry, and `annotate` loaded a second reference library rather than being handed the one the run already had.
    edges = _group_imports()
    seen: dict[str, int] = {}
    cycles: list[list[str]] = []

    def walk(node: str, path: list[str]) -> None:
        if seen.get(node) == 1:
            cycles.append(path[path.index(node):] + [node])
            return
        if seen.get(node) == 2:
            return
        seen[node] = 1
        for other in sorted(edges.get(node, {})):
            walk(other, path + [node])
        seen[node] = 2

    for group in sorted(GROUPS):
        walk(group, [])

    assert not cycles, "\n".join(
        " -> ".join(c) + "\n    " + "\n    ".join(
            edges[a][b][0] for a, b in zip(c, c[1:])) for c in cycles)
