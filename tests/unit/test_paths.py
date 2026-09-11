"""One layout, one resolver: where a run's files go and where they are read."""

from __future__ import annotations

from pathlib import Path

from pipeline.shared import paths, settings


def test_the_config_keys_and_the_layout_agree(tmp_path):
    """DEFAULT_GLOBAL restated three directories the layout already declared,
    and a third set of fallbacks was inlined at the API's call sites - where
    input_dir defaulted to 'inputs' against a layout saying 'library/refs'."""
    for key, name in paths.GLOBAL_KEYS.items():
        assert settings.DEFAULT_GLOBAL["paths"][key] == paths.LAYOUT[name], key


def test_a_config_that_moves_the_runs_moves_where_they_are_read(tmp_path):
    """The launcher read the effective config and the API read _global, so a
    config setting output_dir had its artifacts written to one directory and
    its log tailed in another."""
    from pipeline.orchestration import launch

    cfg = {"paths": {"output_dir": "out/elsewhere"}}
    assert launch.runs_base(tmp_path, cfg) == paths.from_config(
        tmp_path, cfg, "output_dir")
    assert launch.runs_base(tmp_path, cfg).name == "elsewhere"


def test_an_unset_key_falls_back_to_the_layout(tmp_path):
    for key, name in paths.GLOBAL_KEYS.items():
        got = paths.from_config(tmp_path, {}, key)
        assert got == (tmp_path / paths.LAYOUT[name]).resolve(), key


def test_the_autopilot_log_is_not_inside_the_runs_directory(tmp_path):
    """It was runs_dir().parent, so it moved whenever a config moved the runs
    and `rm -rf out/` deleted it along with them."""
    from pipeline.api import jobs

    log = jobs.autopilot_log_path()
    runs = paths.resolve(jobs.ROOT, "runs")
    assert runs not in log.parents
    assert log.parent == paths.resolve(jobs.ROOT, "logs")


def test_every_resolved_directory_is_absolute_and_made(tmp_path):
    for name in paths.LAYOUT:
        got = paths.resolve(tmp_path, name)
        assert got.is_absolute() and got.is_dir(), name
