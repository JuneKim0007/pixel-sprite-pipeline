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
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

ROUTE_DECORATORS = {"get", "post", "put", "delete", "patch"}

# Builtins that mean "a defect", as opposed to the taxonomy which means "a
# message". SystemExit is here because a CLI raising one is correct and a route
# handler raising one is not - which is exactly the distinction reachability
# draws, so it is listed rather than exempted.
BUILTIN_FAILURES = {
    "ValueError", "FileNotFoundError", "KeyError", "RuntimeError", "TypeError",
    "PermissionError", "NotADirectoryError", "IsADirectoryError", "OSError",
    "TimeoutError", "IndexError", "SystemExit", "NotImplementedError",
    "Exception", "AttributeError", "ArithmeticError", "AssertionError",
}

# The escape hatch, and the reason it demands a reason. "This is a defect, not
# a message" is a real claim about a call site and someone should be able to
# read it and disagree. A bare marker is an omission wearing a hatch.
MARKER = re.compile(r"#\s*not-a-message:(?P<reason>.*)$")


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    exception: str
    function: str
    reason_missing: bool = False


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


def _own_body(node: ast.FunctionDef) -> list[ast.AST]:
    """Every descendant of a function's body, not descending into nested defs.

    A nested FunctionDef/AsyncFunctionDef is Index's territory, not this
    function's: Index.__init__ registers it separately under its own name,
    and (since reachable() now enqueues nested defs directly - see there)
    it is scanned for its own raises and its own calls independently. Code
    physically inside it belongs to it, not to the function lexically
    containing it, so both raise-scanning and call-scanning stop here.
    """
    found: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        found.append(child)
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue  # separately indexed and separately enqueued
        stack.extend(ast.iter_child_nodes(child))
    return found


def _called_names(node: ast.FunctionDef) -> set[str]:
    # Scoped to node's own body: a name called only inside a nested def's
    # body is that nested def's own edge. It used to matter that this walked
    # into nested defs too, because that was the only way a closure's calls
    # were ever attributed to anything - but reachable() now enqueues a
    # nested def directly whenever its enclosing function is live, whether
    # or not the nested def is ever called by name. So the nested def gets
    # its own _called_names() call when it is dequeued, and walking into it
    # from here would just find the same edges a second time.
    names = set()
    for child in _own_body(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _raises_in_own_body(node: ast.FunctionDef) -> list[ast.Raise]:
    """Every `raise` in a function's own body, not descending into nested defs.

    ast.walk(node) would also visit any FunctionDef/AsyncFunctionDef nested
    inside node - but Index.__init__ already walks the whole tree and
    registers that nested function under its own name. Counting its raises
    here too would attribute the same physical raise to both functions,
    and `sorted(set(found))` cannot dedupe them because `.function` differs.
    """
    return [n for n in _own_body(node) if isinstance(n, ast.Raise)]


def _comment_map(source: str) -> dict[int, str]:
    """Real comment tokens in a file, by line number.

    tokenize is the only reliable way to tell a `#` inside a string literal
    from an actual comment; a regex over raw text cannot, and a false match
    there would silently suppress a real violation - the worst failure mode
    this tool has, since the marker is the only thing standing between
    "flagged" and "suppressed".

    If the file fails to tokenize at all (encoding oddities, syntax tokenize's
    grammar can't handle), the fallback is no comments found: every raise in
    that file is then scanned against an empty map, finds no marker, and gets
    flagged. Failing to flag is never the safe direction here.
    """
    comments: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comments[tok.start[0]] = tok.string
    except Exception:
        return {}
    return comments


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
        # A def lexically nested inside a live function is live too, whether
        # or not its name ever appears in a Call. It was written there to
        # run - as a callback, a thread target, a sort key, a decorator -
        # and a `threading.Thread(target=helper)` never calls `helper` by
        # name, it just passes it. Requiring a Call as proof of reachability
        # would invert this checker's stated bias: a false positive costs
        # one `# not-a-message:` marker, a false negative costs a 500 in
        # front of a user. So enqueue nested defs unconditionally.
        for child in ast.walk(node):
            if child is node:
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if (str(path), child.name) not in seen:
                    queue.append((path, child))
    return seen


def _raised_name(node: ast.Raise) -> str | None:
    if node.exc is None:
        return None                      # a bare `raise` re-raises; not a site
    exc = node.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Attribute):
        return exc.attr
    return None


def _marker(comments: dict[int, str], node: ast.Raise) -> tuple[bool, str]:
    """Whether the raise carries a marker, and the reason given.

    Searched across the whole statement, because a raise with a long message
    wraps and the comment lands on the closing paren. `comments` maps line
    number to real comment text (see `_comment_map`), so a marker-shaped
    string inside the raised message itself is never mistaken for one.
    """
    last = getattr(node, "end_lineno", node.lineno) or node.lineno
    for line_no in range(node.lineno, last + 1):
        comment = comments.get(line_no)
        if comment is None:
            continue
        found = MARKER.search(comment)
        if found:
            return True, found.group("reason").strip()
    return False, ""


def violations(index: Index) -> list[Violation]:
    """Every builtin raise a request can reach."""
    live = reachable(index, entry_points(index))
    found: list[Violation] = []
    # A comment map is a full tokenize pass over a file's text and depends
    # only on the file, not on which function within it is being scanned -
    # build each file's map once and reuse it, rather than retokenizing once
    # per function that happens to live in that file.
    comments_by_path: dict[Path, dict[int, str]] = {}
    for name in index.definitions:
        for path, node in index.definitions[name]:
            if (str(path), node.name) not in live:
                continue
            if path not in comments_by_path:
                try:
                    text = path.read_text()
                except OSError:
                    text = ""
                comments_by_path[path] = _comment_map(text)
            comments = comments_by_path[path]
            for child in _raises_in_own_body(node):
                raised = _raised_name(child)
                if raised not in BUILTIN_FAILURES:
                    continue
                marked, reason = _marker(comments, child)
                if marked and reason:
                    continue
                found.append(Violation(path, child.lineno, raised, node.name,
                                       reason_missing=marked))
    return sorted(set(found), key=lambda v: (str(v.path), v.line))


def report(found: list[Violation]) -> str:
    """The violations, in the shape `make check` prints things."""
    lines = []
    for v in found:
        why = ("marker needs a reason after the colon" if v.reason_missing
               else f"reachable from a route, via {v.function}()")
        lines.append(f"  {v.path}:{v.line}\n"
                     f"    raise {v.exception} - {why}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import sys as _sys

    roots = [Path(a) for a in (argv if argv is not None else _sys.argv[1:])]
    if not roots:
        roots = [Path("pipeline")]
    found = violations(Index(roots))
    if found:
        print(report(found))
        print(f"  {len(found)} unnamed failure(s) on a path a request can reach")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())  # not-a-message: CLI exit code, this is not a route
