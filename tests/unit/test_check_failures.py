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


def test_a_builtin_raise_a_request_can_reach_is_flagged(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        return load()

def load():
    raise FileNotFoundError("gone")
"""})
    found = cf.violations(cf.Index([tmp_path]))
    assert [(v.function, v.exception) for v in found] == [("load", "FileNotFoundError")]


def test_a_builtin_raise_no_request_can_reach_is_left_alone(tmp_path):
    _tree(tmp_path, {"cli.py": """
def main():
    raise SystemExit(2)
"""})
    assert cf.violations(cf.Index([tmp_path])) == []


def test_a_named_failure_is_not_a_violation(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise NotFound("look", "x")
"""})
    assert cf.violations(cf.Index([tmp_path])) == []


def test_a_marker_with_a_reason_suppresses_the_flag(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise SystemExit(2)  # not-a-message: CLI exit code, never reaches HTTP
"""})
    assert cf.violations(cf.Index([tmp_path])) == []


def test_a_marker_with_no_reason_is_itself_the_failure(tmp_path):
    # The whole point of the hatch is that using it says something out loud.
    # A bare marker says nothing and would let the count creep back silently.
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise SystemExit(2)  # not-a-message:
"""})
    found = cf.violations(cf.Index([tmp_path]))
    assert len(found) == 1
    assert found[0].reason_missing is True


def test_a_marker_on_a_multiline_raise_is_found(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise ValueError(
            "a long message"
        )  # not-a-message: internal invariant, callers cannot trigger it
"""})
    assert cf.violations(cf.Index([tmp_path])) == []


def test_the_report_names_the_file_line_and_exception(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise FileNotFoundError("gone")
"""})
    text = cf.report(cf.violations(cf.Index([tmp_path])))
    assert "api.py" in text
    assert "FileNotFoundError" in text
    assert "listing" in text


def test_exit_code_is_one_when_something_is_unnamed(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "x")
    def listing(self, req):
        raise ValueError("nope")
"""})
    assert cf.main([str(tmp_path)]) == 1


def test_a_raise_inside_a_nested_function_is_counted_once(tmp_path):
    # ast.walk(listing) would also visit helper's body, and Index registers
    # helper under its own name too - the same physical raise must not turn
    # into two Violations (one per attribution) that `sorted(set(...))` then
    # fails to dedupe because their `.function` differs.
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        def helper():
            raise ValueError("boom")
        return helper()
"""})
    found = cf.violations(cf.Index([tmp_path]))
    assert len(found) == 1
    assert found[0].function == "helper"
    assert found[0].exception == "ValueError"


def test_a_marker_shaped_string_inside_the_message_does_not_suppress(tmp_path):
    # _marker used to regex raw source text, so a raise whose own message
    # happens to contain marker-shaped text would read as carrying a real
    # marker and get suppressed - the worst failure mode this tool has.
    _tree(tmp_path, {"api.py": '''
class Looks(BaseRouter):
    @get("/looks", "list them")
    def listing(self, req):
        raise ValueError("oops # not-a-message: pretending")
'''})
    found = cf.violations(cf.Index([tmp_path]))
    assert [(v.function, v.exception) for v in found] == [("listing", "ValueError")]
    assert found[0].reason_missing is False


def test_exit_code_is_zero_when_clean(tmp_path):
    _tree(tmp_path, {"api.py": """
class Looks(BaseRouter):
    @get("/looks", "x")
    def listing(self, req):
        raise NotFound("look", "x")
"""})
    assert cf.main([str(tmp_path)]) == 0
