#!/usr/bin/env python3
"""Report comments and docstrings longer than the repo limit."""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCAN = ["pipeline", "tools", "web/js", "tests", "library", "scripts", "Makefile"]

SKIP_DIRS = {"ComfyUI", "out", "__pycache__", "node_modules", ".git", "training_set"}

COMMENT_LIMIT = 1
DOCSTRING_LIMIT = 2


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    kind: str
    length: int
    first: str

    @property
    def limit(self) -> int:
        return DOCSTRING_LIMIT if self.kind == "docstring" else COMMENT_LIMIT


def _rendered_lines(text: str) -> int:
    return len(text.strip().splitlines()) or 1


def _run_findings(path: Path, kind: str, runs: list[tuple[int, list[str]]],
                  limit: int) -> list[Finding]:
    out = []
    for line, lines in runs:
        if len(lines) > limit:
            out.append(Finding(path, line, kind, len(lines), lines[0].strip()))
    return out


def _hash_runs(text: str) -> list[tuple[int, list[str]]]:
    """Consecutive `#` comment lines are one comment, hanging continuations included."""
    runs: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = column = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.lstrip()
        if number == 1 and stripped.startswith("#!"):
            continue
        whole_line = stripped.startswith("#") or stripped.startswith("@#")
        at = raw.find("#")
        if whole_line and current:
            current.append(stripped)
            continue
        if current:
            runs.append((start, current))
            current = []
        if whole_line:
            start, column, current = number, at, [stripped]
        elif at >= 0 and not _in_quotes(raw, at):
            start, column, current = number, at, [raw[at:].strip()]
    if current:
        runs.append((start, current))
    return runs


def _in_quotes(raw: str, at: int) -> bool:
    """A `#` inside a quoted scalar is not a comment."""
    quote = ""
    for char in raw[:at]:
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
    return bool(quote)


def scan_python(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    findings += _python_comments(path, text)
    findings += _python_docstrings(path, text)
    return findings


def _python_comments(path: Path, text: str) -> list[Finding]:
    """Whole-line `#` runs, via tokenize so strings never register."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    runs: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = previous = column = 0
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        line, at = token.start[0], token.start[1]
        own_line = token.line[:at].strip() == ""
        if current and line == previous + 1 and own_line and column == at:
            current.append(token.string)
        else:
            if current:
                runs.append((start, current))
            start, column, current = line, at, [token.string]
        previous = line
    if current:
        runs.append((start, current))
    return _run_findings(path, "comment", runs, COMMENT_LIMIT)


def _python_docstrings(path: Path, text: str) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    findings: list[Finding] = []
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        doc = ast.get_docstring(node, clean=False)
        if doc is None:
            continue
        length = _rendered_lines(doc)
        if length > DOCSTRING_LIMIT:
            first = doc.strip().splitlines()[0].strip()
            findings.append(
                Finding(path, node.body[0].lineno, "docstring", length, first))
    return findings


# A `/` opens a regex only where the previous significant character cannot end an expression.
REGEX_CANNOT_FOLLOW = set("])}") | set("abcdefghijklmnopqrstuvwxyz"
                                       "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")


def scan_javascript(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    line_runs: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = previous = 0

    index, line, length = 0, 1, len(text)
    last_significant = ""
    while index < length:
        char = text[index]
        if char == "\n":
            line += 1
            index += 1
            continue
        if char in " \t\r":
            index += 1
            continue
        pair = text[index:index + 2]
        if pair == "//":
            end = text.find("\n", index)
            end = length if end == -1 else end
            body = text[index:end]
            own_line = text[:index].rsplit("\n", 1)[-1].strip() == ""
            if own_line:
                if current and line == previous + 1:
                    current.append(body)
                else:
                    if current:
                        line_runs.append((start, current))
                    start, current = line, [body]
                previous = line
            index = end
            continue
        if pair == "/*":
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            body = text[index:end]
            span = body.count("\n") + 1
            if span > COMMENT_LIMIT:
                first = body.splitlines()[0].strip()
                findings.append(Finding(path, line, "block", span, first))
            line += body.count("\n")
            index = end
            last_significant = ""
            continue
        if char in "\"'`":
            end = _skip_string(text, index)
            line += text.count("\n", index, end)
            index = end
            last_significant = char
            continue
        if char == "/" and last_significant not in REGEX_CANNOT_FOLLOW:
            end = _skip_regex(text, index)
            line += text.count("\n", index, end)
            index = end
            last_significant = "/"
            continue
        last_significant = char
        index += 1

    if current:
        line_runs.append((start, current))
    findings += _run_findings(path, "comment", line_runs, COMMENT_LIMIT)
    return sorted(findings, key=lambda f: f.line)


def _skip_string(text: str, index: int) -> int:
    quote = text[index]
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        if quote != "`" and char == "\n":
            return index
        index += 1
    return index


def _skip_regex(text: str, index: int) -> int:
    index += 1
    in_class = False
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return index + 1
        elif char == "\n":
            return index
        index += 1
    return index


def scan_hash(path: Path, text: str) -> list[Finding]:
    return _run_findings(path, "comment", _hash_runs(text), COMMENT_LIMIT)


SCANNERS = {
    ".py": scan_python,
    ".js": scan_javascript,
    ".mjs": scan_javascript,
    ".yaml": scan_hash,
    ".yml": scan_hash,
    ".sh": scan_hash,
}


def files() -> list[Path]:
    found: list[Path] = []
    for entry in SCAN:
        target = ROOT / entry
        if target.is_file():
            found.append(target)
            continue
        for path in sorted(target.rglob("*")):
            if not path.is_file():
                continue
            if SKIP_DIRS & set(path.relative_to(ROOT).parts):
                continue
            if path.suffix in SCANNERS:
                found.append(path)
    return found


def language(path: Path) -> str:
    if path.name == "Makefile":
        return "makefile"
    return {".py": "python", ".js": "javascript", ".mjs": "javascript",
            ".yaml": "yaml", ".yml": "yaml", ".sh": "shell"}[path.suffix]


def scan(paths: list[Path], limit: int | None) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        scanner = scan_hash if path.name == "Makefile" else SCANNERS[path.suffix]
        for finding in scanner(path, text):
            if limit is None or finding.length > limit:
                findings.append(finding)
    return findings


def _expand(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        path = path.resolve()
        if path.is_dir():
            found += [p for p in sorted(path.rglob("*"))
                      if p.is_file() and p.suffix in SCANNERS
                      and not SKIP_DIRS & set(p.parts)]
        else:
            found.append(path)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="report only comments longer than N lines")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)

    targets = _expand(args.paths) if args.paths else files()
    findings = scan(targets, args.limit)
    for finding in sorted(findings, key=lambda f: (str(f.path), f.line)):
        try:
            shown = finding.path.relative_to(ROOT)
        except ValueError:
            shown = finding.path
        print(f"{shown}:{finding.line}  {finding.kind:9} "
              f"{finding.length:3} lines  {finding.first}")

    counts: dict[str, int] = {}
    for finding in findings:
        counts[language(finding.path)] = counts.get(language(finding.path), 0) + 1
    print(f"\n{len(findings)} over limit", file=sys.stderr)
    for name in sorted(counts):
        print(f"  {name:11} {counts[name]}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())  # not-a-message: CLI exit code, this is not a route
