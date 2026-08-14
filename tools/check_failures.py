"""Every failure a user can reach must carry a name.

`pipeline/shared/errors.py` draws the rule this enforces: a ValueError reaching
the server is a bug, and a PixelError is a message to the user. That rule is
only worth anything if something checks it - the count of builtin raises was 79
when it was written and 80 a refactor later, because nothing did.

So: walk outward from every route handler and flag any builtin raise reachable
from one. Not every builtin raise - the target was never zero. A CLI exit code
and an abstract method are correctly builtins, and converting them would make
every failure look like a user message, which is the mirror of the bug.

Resolution is by bare name and therefore over-approximates: two functions called
`load` are treated as one. That is the right direction to be wrong in. A false
positive costs one marker with a reason attached; a false negative costs a 500
in front of someone.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTE_DECORATORS = {"get", "post", "put", "delete", "patch"}


class Index:
    """Every function defined under `roots`, by bare name."""

    def __init__(self, roots: list[Path]):
        self.definitions: dict[str, list[tuple[Path, ast.FunctionDef]]] = {}
        self.trees: dict[Path, ast.Module] = {}
        for root in roots:
            for path in sorted(Path(root).rglob("*.py")):
                try:
                    tree = ast.parse(path.read_text())
                except (OSError, SyntaxError):
                    continue
                self.trees[path] = tree
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.definitions.setdefault(node.name, []).append(
                            (path, node))


def _decorator_names(node: ast.FunctionDef) -> set[str]:
    names = set()
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def entry_points(index: Index) -> list[tuple[Path, ast.FunctionDef]]:
    """Every route handler: a method carrying @get / @post / @put."""
    return [(path, node)
            for defs in index.definitions.values()
            for path, node in defs
            if _decorator_names(node) & ROUTE_DECORATORS]


def _called_names(node: ast.FunctionDef) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def reachable(index: Index, entries) -> set[tuple[str, str]]:
    """Everything callable from an entry point, transitively."""
    seen: set[tuple[str, str]] = set()
    queue = list(entries)
    while queue:
        path, node = queue.pop()
        key = (str(path), node.name)
        if key in seen:
            continue
        seen.add(key)
        for name in _called_names(node):
            for target in index.definitions.get(name, []):
                if (str(target[0]), target[1].name) not in seen:
                    queue.append(target)
    return seen
