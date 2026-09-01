"""What `./run.py` decides before the pipeline starts.

main() picks the run id, resolves the gate, snapshots the config, seeds a
resume and names the output. None of that was executed by a test, and none of
it needs a GPU: the only thing faked here is runner.run, which is where the
work would begin.
"""

from __future__ import annotations

import json

import pytest
import yaml

import run as run_cli
from pipeline.generation import runner
from pipeline.generation.stage import Context
from pipeline.orchestration import artifacts as artifacts_io
from pipeline.shared import paths


@pytest.fixture
def home(root, tmp_path, monkeypatch):
    (tmp_path / "library" / "configs").mkdir(parents=True, exist_ok=True)
    for name in ("_global",):
        src = paths.resolve(root, "configs") / f"{name}.yaml"
        if src.exists():
            (tmp_path / "library" / "configs" / f"{name}.yaml").write_text(
                src.read_text())
    monkeypatch.setattr(run_cli, "ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def calls(monkeypatch):
    """runner.run, recorded rather than run. It is where the GPU would start."""
    seen = []

    def fake(built, ctx, **kw):
        seen.append({"stages": [s.name for s in built], "ctx": ctx, **kw})
        return ctx

    monkeypatch.setattr(runner, "run", fake)
    return seen


def config(home, filename="job", stages=("pose",), **extra):
    path = home / f"{filename}.yaml"
    body = {"pipeline": {"stages": list(stages)}, **extra}
    path.write_text(yaml.safe_dump(body, sort_keys=False))
    return path


def main(monkeypatch, *argv):
    monkeypatch.setattr(run_cli.sys, "argv", ["run.py", *argv])
    return run_cli.main()


def test_a_run_names_its_directory_after_the_config(home, calls, monkeypatch):
    cfg = config(home, "knight")
    assert main(monkeypatch, str(cfg), "--outdir", str(home / "out")) == 0

    made = [p.name for p in (home / "out").iterdir()]
    assert len(made) == 1 and made[0].endswith("_knight")


def test_name_overrides_the_config_stem(home, calls, monkeypatch):
    cfg = config(home, "knight")
    main(monkeypatch, str(cfg), "--outdir", str(home / "out"), "--name", "wolf")

    assert [p.name for p in (home / "out").iterdir()][0].endswith("_wolf")


def test_the_config_can_name_the_run_itself(home, calls, monkeypatch):
    cfg = config(home, "knight", name="from_config")
    main(monkeypatch, str(cfg), "--outdir", str(home / "out"))

    assert [p.name for p in (home / "out").iterdir()][0].endswith("_from_config")


def test_run_id_is_used_exactly_as_given(home, calls, monkeypatch):
    cfg = config(home, "knight")
    main(monkeypatch, str(cfg), "--outdir", str(home / "out"),
         "--run-id", "exactly_this")

    assert [p.name for p in (home / "out").iterdir()] == ["exactly_this"]
    assert calls[0]["ctx"].run_id == "exactly_this"


def test_the_config_is_snapshotted_into_the_run(home, calls, monkeypatch):
    cfg = config(home, "knight")
    main(monkeypatch, str(cfg), "--outdir", str(home / "out"),
         "--run-id", "r1")

    snapshot = home / "out" / "r1" / "config.yaml"
    assert snapshot.exists(), "a run must carry the config it ran"
    assert yaml.safe_load(snapshot.read_text())["pipeline"]["stages"] == ["pose"]


def test_a_missing_config_says_so_and_does_not_start(home, calls, monkeypatch):
    with pytest.raises(SystemExit, match="no such config"):
        main(monkeypatch, str(home / "absent.yaml"))
    assert calls == []


def test_a_config_without_stages_is_refused(home, calls, monkeypatch):
    bad = home / "bad.yaml"
    bad.write_text("name: nothing\n")
    with pytest.raises(SystemExit, match="pipeline.stages"):
        main(monkeypatch, str(bad))
    assert calls == []


def test_list_stages_prints_and_exits_without_running(home, calls, monkeypatch,
                                                      capsys):
    assert main(monkeypatch, "--list-stages") == 0
    assert "registered stages:" in capsys.readouterr().out
    assert calls == []


def test_explain_prints_the_plan_and_runs_nothing(home, calls, monkeypatch,
                                                  capsys):
    cfg = config(home, "knight")
    assert main(monkeypatch, str(cfg), "--explain") == 0
    assert "pose" in capsys.readouterr().out
    assert calls == [], "--explain must not start the pipeline"
    assert not (home / "out").exists(), "--explain must not make a run directory"


def test_no_config_at_all_is_an_argparse_error(home, calls, monkeypatch):
    with pytest.raises(SystemExit):
        main(monkeypatch)
    assert calls == []


# ------------------------------------------------------------------- gating

def test_the_config_gate_reaches_the_runner(home, calls, monkeypatch):
    cfg = config(home, "knight", stages=("pose", "depth"),
                 pipeline={"stages": ["pose", "depth"], "stop_after": "pose"})
    main(monkeypatch, str(cfg), "--outdir", str(home / "out"))

    assert calls[0]["stop_after"] == "pose"


def test_stop_after_overrides_the_config(home, calls, monkeypatch):
    cfg = config(home, "knight",
                 pipeline={"stages": ["pose", "depth"], "stop_after": "pose"})
    main(monkeypatch, str(cfg), "--outdir", str(home / "out"),
         "--stop-after", "depth")

    assert calls[0]["stop_after"] == "depth"


def test_no_gate_beats_both(home, calls, monkeypatch):
    cfg = config(home, "knight",
                 pipeline={"stages": ["pose", "depth"], "stop_after": "pose"})
    main(monkeypatch, str(cfg), "--outdir", str(home / "out"),
         "--stop-after", "depth", "--no-gate")

    assert calls[0]["stop_after"] is None


# ------------------------------------------------------------------ resuming

def _finished_run(home, calls, monkeypatch, run_id="r1"):
    cfg = config(home, "knight")
    main(monkeypatch, str(cfg), "--outdir", str(home / "out"), "--run-id", run_id)
    return home / "out" / run_id


def test_a_resume_reads_back_what_the_first_run_produced(home, calls, monkeypatch):
    outdir = _finished_run(home, calls, monkeypatch)
    artifacts_io.save(outdir, {"skeletons": ["a.png"]}, ["pose"])
    calls.clear()

    assert main(monkeypatch, "--resume", "r1", "--outdir", str(home / "out")) == 0
    assert calls[0]["skip"] == {"pose"}, "a finished stage is skipped"
    assert calls[0]["ctx"].artifacts["skeletons"] == ["a.png"]


def test_a_resume_uses_the_config_the_run_snapshotted(home, calls, monkeypatch):
    _finished_run(home, calls, monkeypatch)
    (home / "knight.yaml").unlink()
    calls.clear()

    assert main(monkeypatch, "--resume", "r1", "--outdir", str(home / "out")) == 0
    assert calls[0]["stages"] == ["pose"], "the snapshot is what a resume runs"


def test_resuming_a_run_that_is_not_there_says_which(home, calls, monkeypatch):
    with pytest.raises(SystemExit, match="no such run to resume"):
        main(monkeypatch, "--resume", "ghost", "--outdir", str(home / "out"))
    assert calls == []


def test_a_run_without_a_snapshot_cannot_be_resumed(home, calls, monkeypatch):
    outdir = _finished_run(home, calls, monkeypatch)
    (outdir / "config.yaml").unlink()
    calls.clear()

    with pytest.raises(SystemExit, match="cannot resume"):
        main(monkeypatch, "--resume", "r1", "--outdir", str(home / "out"))
    assert calls == []


def test_a_resume_keeps_the_run_id_it_resumed(home, calls, monkeypatch):
    _finished_run(home, calls, monkeypatch, "keepme")
    calls.clear()
    main(monkeypatch, "--resume", "keepme", "--outdir", str(home / "out"))

    assert calls[0]["ctx"].run_id == "keepme"


# ------------------------------------------------------------------- results

def test_what_the_run_produced_is_saved_even_when_it_raises(home, calls,
                                                            monkeypatch):
    cfg = config(home, "knight")

    def explode(built, ctx, **kw):
        ctx.artifacts["half"] = "done"
        ctx.completed.append("pose")
        raise RuntimeError("stage blew up")

    monkeypatch.setattr(runner, "run", explode)
    with pytest.raises(RuntimeError, match="stage blew up"):
        main(monkeypatch, str(cfg), "--outdir", str(home / "out"),
             "--run-id", "r1")

    seeded, completed = artifacts_io.load(home / "out" / "r1")
    assert seeded["half"] == "done", "a crashed run still records its artifacts"
    assert completed == ["pose"]


def test_a_gated_run_tells_you_how_to_resume(home, calls, monkeypatch, capsys):
    cfg = config(home, "knight")

    def gated(built, ctx, **kw):
        ctx.stopped_at = "pose"
        return ctx

    monkeypatch.setattr(runner, "run", gated)
    main(monkeypatch, str(cfg), "--outdir", str(home / "out"), "--run-id", "r1")

    assert "--resume r1" in capsys.readouterr().out


def test_the_context_carries_the_run_directory(home, calls, monkeypatch):
    cfg = config(home, "knight")
    main(monkeypatch, str(cfg), "--outdir", str(home / "out"), "--run-id", "r1")

    ctx = calls[0]["ctx"]
    assert isinstance(ctx, Context)
    assert ctx.outdir == home / "out" / "r1"
    assert json.loads(json.dumps(list(ctx.config["pipeline"]["stages"]))) == ["pose"]
