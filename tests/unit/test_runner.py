"""What the runner does with a plan, checked with stages that only record.

`test_generate.py` covers planning — which batches form, in what order. Nothing
executed `runner.run`, so resume, the gate, artifact accumulation and the
parallel path were all unverified. A Stage is an ordinary object here, so the
fakes need no patching: they record that they ran and return what they declared.
"""

from __future__ import annotations

import threading

import pytest

from pipeline.generation import runner
from pipeline.generation.stage import Context, Resource, Stage
from pipeline.generation.runner import PipelineError


def fake(name, *, gives=(), needs=(), resource=Resource.CPU, log=None,
         fails=False, prepares=None):
    """A stage that records that it ran and returns exactly what it declared."""

    def prepare(self, ctx):
        if log is not None:
            log.append(f"prepare:{name}")
        return prepares or {}

    def run(self, ctx, prep):
        if log is not None:
            log.append(name)
        if fails:
            raise RuntimeError(f"{name} blew up")
        return {g: f"{name}-{g}" for g in self.gives}

    return type(f"Fake{name.title()}", (Stage,), {
        "name": name,
        "resource": resource,
        "needs": frozenset(needs),
        "gives": frozenset(gives),
        "prepare": prepare,
        "run": run,
    })()


@pytest.fixture
def ctx(tmp_path):
    return Context(root=tmp_path, outdir=tmp_path, run_id="r",
                   config={"cooling": {"enabled": False}}, artifacts={})


def test_stages_run_in_dependency_order(ctx):
    log = []
    stages = [fake("a", gives=["x"], log=log),
              fake("b", needs=["x"], gives=["y"], log=log),
              fake("c", needs=["y"], log=log)]
    runner.run(stages, ctx, verbose=False)

    assert [n for n in log if not n.startswith("prepare:")] == ["a", "b", "c"]
    assert ctx.completed == ["a", "b", "c"]


def test_what_a_stage_returns_reaches_the_next_one(ctx):
    runner.run([fake("a", gives=["x"]), fake("b", needs=["x"], gives=["y"])],
               ctx, verbose=False)
    assert ctx.artifacts == {"x": "a-x", "y": "b-y"}


def test_prepare_runs_before_run(ctx):
    log = []
    runner.run([fake("a", gives=["x"], log=log, prepares={"k": 1})], ctx,
               verbose=False)
    assert log == ["prepare:a", "a"]


def test_a_stage_that_withholds_what_it_declared_is_refused(ctx):
    class Liar(Stage):
        name = "liar"
        gives = frozenset({"x"})

        def run(self, c, p):
            return {}

    with pytest.raises(PipelineError):
        runner.run([Liar()], ctx, verbose=False)


def test_skip_does_not_re_run_a_completed_stage(ctx):
    log = []
    ctx.artifacts["x"] = "seeded"
    stages = [fake("a", gives=["x"], log=log),
              fake("b", needs=["x"], gives=["y"], log=log)]
    runner.run(stages, ctx, verbose=False, skip={"a"})

    assert "a" not in log, "a was skipped and must not run"
    assert ctx.completed == ["a", "b"], "a still counts as completed"
    assert ctx.artifacts["x"] == "seeded", "the seeded artifact survives"


def test_stop_after_halts_and_records_where(ctx):
    log = []
    stages = [fake("a", gives=["x"], log=log),
              fake("b", needs=["x"], gives=["y"], log=log),
              fake("c", needs=["y"], log=log)]
    runner.run(stages, ctx, verbose=False, stop_after="b")

    assert [n for n in log if not n.startswith("prepare:")] == ["a", "b"]
    assert ctx.stopped_at == "b"
    assert "c" not in ctx.completed


def test_a_completed_run_records_no_stop(ctx):
    runner.run([fake("a", gives=["x"])], ctx, verbose=False)
    assert ctx.stopped_at is None


def test_stop_after_a_stage_not_in_the_pipeline_names_the_stages(ctx):
    with pytest.raises(PipelineError, match="stop_after 'zzz' is not in this"):
        runner.run([fake("a", gives=["x"])], ctx, verbose=False, stop_after="zzz")


def test_independent_cpu_stages_share_a_batch_and_both_land(ctx):
    log = []
    lock = threading.Lock()

    def note(n):
        with lock:
            log.append(n)

    a = fake("a", gives=["x"])
    b = fake("b", gives=["y"])
    for stage in (a, b):
        original = stage.run
        stage_name = stage.name
        stage.run = (lambda c, p, _o=original, _n=stage_name:
                     (note(_n), _o(c, p))[1])
    runner.run([a, b], ctx, verbose=False)

    assert sorted(log) == ["a", "b"]
    assert sorted(ctx.completed) == ["a", "b"]
    assert ctx.artifacts == {"x": "a-x", "y": "b-y"}


def test_a_stage_that_raises_stops_the_run(ctx):
    log = []
    stages = [fake("a", gives=["x"], log=log),
              fake("b", needs=["x"], gives=["y"], log=log, fails=True),
              fake("c", needs=["y"], log=log)]
    with pytest.raises(RuntimeError, match="b blew up"):
        runner.run(stages, ctx, verbose=False)
    assert "c" not in log, "a stage after the failure must not run"


def test_cooling_rests_between_gpu_batches_but_not_after_the_last(ctx, monkeypatch):
    rested = []
    monkeypatch.setattr(runner.cooling, "rest",
                        lambda config, **kw: rested.append(kw) or 0.0)
    stages = [fake("a", gives=["x"], resource=Resource.GPU),
              fake("b", needs=["x"], gives=["y"], resource=Resource.GPU)]
    runner.run(stages, ctx, verbose=False)

    assert [r["last"] for r in rested] == [False, True]


def test_a_gated_run_does_not_count_the_gpu_work_it_will_not_do(ctx, monkeypatch):
    rested = []
    monkeypatch.setattr(runner.cooling, "rest",
                        lambda config, **kw: rested.append(kw) or 0.0)
    stages = [fake("a", gives=["x"], resource=Resource.GPU),
              fake("b", needs=["x"], gives=["y"], resource=Resource.GPU)]
    runner.run(stages, ctx, verbose=False, stop_after="a")

    assert [r["last"] for r in rested] == [True], \
        "the only gpu batch that will run is the last one"


def test_a_need_nothing_supplies_is_refused_before_anything_runs(ctx):
    """validate() guards the plan; the withholding test above guards the result."""
    log = []
    with pytest.raises(Exception) as caught:
        runner.run([fake("a", needs=["nobody_gives_this"], log=log)], ctx,
                   verbose=False)
    assert log == [], "the run must be refused before a stage executes"
    assert "nobody_gives_this" in str(caught.value)
