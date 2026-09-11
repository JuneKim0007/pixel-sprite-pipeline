"""One gate, three callers. Until 2026-09-11 each checked a different subset."""

from __future__ import annotations

import ast
import pathlib

import pytest

from pipeline.orchestration import admission

GOOD = {"pipeline": {"stages": ["pose"]}}


def test_a_config_with_nothing_wrong_has_nothing_to_say(tmp_path):
    assert admission.problems(tmp_path, GOOD) == []


@pytest.mark.parametrize("label,cfg,expect", [
    ("a setting no field declares", {"canonical": {"stps": 30}}, "canonical.steps"),
    ("a stage that does not exist", {"pipeline": {"stages": ["nope"]}}, "nope"),
    ("a rig that does not exist", {"rig": "griffin"}, "griffin"),
    ("a reference naming no file",
     {"references": {"identity": ["absent.png"]}}, "absent.png"),
    ("the replaced images key",
     {"references": {"images": ["a.png"]}}, "typed roles"),
])
def test_each_thing_a_run_cannot_start_with_is_named(tmp_path, label, cfg, expect):
    found = admission.problems(tmp_path, {**GOOD, **cfg})
    assert found, f"{label} was admitted"
    assert expect in " ".join(found), f"{label}: {found}"


@pytest.mark.parametrize("gone", ["_check_settings", "_check_stack",
                                  "_check_rig", "_check_references"])
def test_the_queue_kept_no_check_of_its_own(gone):
    """_check_references was a second reference parser with its own wording."""
    from pipeline.orchestration import queue as q

    assert not hasattr(q, gone), f"queue kept {gone}; the two will drift again"


def test_the_deprecation_message_has_one_source():
    from pipeline.refs import references as refs_mod

    assert refs_mod.IMAGES_REPLACED in " ".join(
        admission.problems(pathlib.Path("."),
                           {**GOOD, "references": {"images": ["a.png"]}}))


LAUNCHERS = ["run.py", "pipeline/api/runs.py", "pipeline/orchestration/queue.py"]


@pytest.mark.parametrize("path", LAUNCHERS)
def test_every_launcher_reaches_the_one_gate(path):
    """A fourth way to start a run must go through it too."""
    tree = ast.parse(pathlib.Path(path).read_text())
    reached = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "problems"
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "admission"
        for n in ast.walk(tree))
    assert reached, f"{path} starts a run without asking admission.problems"



def test_the_run_route_refuses_instead_of_launching(tmp_path, monkeypatch):
    """This route called no preflight at all: it started what the queue refused."""
    import yaml

    from pipeline.api import runs as runs_api
    from pipeline.shared import paths
    from pipeline.shared.errors import Invalid

    launched = []
    monkeypatch.setattr(runs_api, "_in_flight", lambda: None)
    monkeypatch.setattr(runs_api.subprocess, "Popen",
                        lambda *a, **kw: launched.append(a))

    name = "_admission_probe"
    cfg = paths.resolve(runs_api.ROOT, "configs") / f"{name}.yaml"
    cfg.write_text(yaml.safe_dump(
        {"pipeline": {"stages": ["pose"]}, "canonical": {"stps": 30}}))
    try:
        with pytest.raises(Invalid, match="canonical.steps"):
            runs_api.start_run(name, None, None)
    finally:
        cfg.unlink()
    assert launched == [], "a config the queue refuses was started anyway"


def test_a_refused_run_leaves_no_run_directory_behind(tmp_path, monkeypatch):
    """The gate moved above mkdir, so a refusal does not litter out/runs."""
    import yaml

    from pipeline.api import runs as runs_api
    from pipeline.shared import paths
    from pipeline.shared.errors import Invalid

    monkeypatch.setattr(runs_api, "_in_flight", lambda: None)
    monkeypatch.setattr(runs_api.subprocess, "Popen", lambda *a, **kw: None)

    name = "_admission_probe2"
    cfg = paths.resolve(runs_api.ROOT, "configs") / f"{name}.yaml"
    cfg.write_text(yaml.safe_dump(
        {"pipeline": {"stages": ["pose"]}, "rig": "griffin"}))
    before = {p.name for p in runs_api.runs_dir().iterdir()}
    try:
        with pytest.raises(Invalid, match="griffin"):
            runs_api.start_run(name, None, None)
    finally:
        cfg.unlink()
    assert {p.name for p in runs_api.runs_dir().iterdir()} == before
