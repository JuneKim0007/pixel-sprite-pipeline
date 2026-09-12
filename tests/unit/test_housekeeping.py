"""Removing what a run leaves behind, by named scope."""

from __future__ import annotations

import pytest

from pipeline.orchestration import housekeeping as hk
from pipeline.shared import paths
from pipeline.shared.errors import Invalid


def populate(root):
    logs = paths.resolve(root, "logs")
    (logs / "web.log").write_text("noisy\n" * 100)
    (logs / "stray.txt").write_text("not a log")
    runs = paths.resolve(root, "runs")
    (runs / "20260101_000000_a").mkdir(parents=True)
    (runs / "20260101_000000_a" / "run.log").write_text("x")
    return logs, runs


def test_a_log_is_truncated_not_unlinked(tmp_path):
    """A log being written is held open; removing it leaves the writer's
    descriptor on an inode nothing can reach, so the space is never returned."""
    logs, _ = populate(tmp_path)
    log = logs / "web.log"
    with log.open("a") as writer:
        hk.wipe(tmp_path, ["logs"])
        assert log.exists(), "the log was unlinked from under its writer"
        assert log.stat().st_size == 0
        writer.write("after\n")


def test_clearing_logs_removes_what_is_not_a_log(tmp_path):
    logs, _ = populate(tmp_path)
    hk.wipe(tmp_path, ["logs"])
    assert not (logs / "stray.txt").exists()


def test_a_runs_own_log_goes_with_the_run(tmp_path):
    """It describes that run and nothing else, so it dies with it."""
    _, runs = populate(tmp_path)
    hk.wipe(tmp_path, ["runs"])
    assert list(runs.iterdir()) == []


def test_clearing_logs_leaves_the_outputs_alone(tmp_path):
    _, runs = populate(tmp_path)
    hk.wipe(tmp_path, ["logs"])
    assert (runs / "20260101_000000_a" / "run.log").exists()


def test_an_unknown_scope_is_refused_rather_than_ignored(tmp_path):
    with pytest.raises(Invalid):
        hk.wipe(tmp_path, ["logs", "everything_else"])


def test_nothing_is_removed_when_one_scope_is_wrong(tmp_path):
    logs, _ = populate(tmp_path)
    with pytest.raises(Invalid):
        hk.wipe(tmp_path, ["nope", "logs"])
    assert (logs / "web.log").stat().st_size > 0


def test_counts_say_what_a_scope_would_remove(tmp_path):
    populate(tmp_path)
    found = hk.counts(tmp_path)
    assert found["logs"] == 2 and found["runs"] == 1
    assert set(found) == set(hk.SCOPES)

