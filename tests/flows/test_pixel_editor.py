from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pipeline import definitive
from pipeline.definitive import cache, layers
from pipeline.shared import limits
from pipeline.shared.errors import Invalid


@pytest.fixture
def img():
    a = np.zeros((96, 96, 3), dtype=np.uint8)
    a[16:80, 16:80] = (200, 60, 60)
    a[30:50, 30:50] = (40, 40, 160)
    return a


@pytest.fixture
def stack():
    return definitive.default_stack()


def test_limits_are_shares_of_the_machine_not_this_laptops_numbers():
    d = limits.describe()
    assert 0 < d["derived"]["threads"] < d["machine"]["cores"]


def test_the_default_stack_runs_without_a_layer_failing(root, img, stack):
    # A raising layer does not kill the run, so it hides a broken import.
    out, facts = definitive.apply_stack(img, stack)
    assert out.ndim == 3, "the stack did not return an image"
    broke = [f"{la['layer']}: {la['error']}" for la in facts["layers"]
             if la.get("error")]
    assert not broke, f"the default stack cannot run: {broke}"
    assert not facts["warnings"], f"the default order warns: {facts['warnings']}"
    assert facts["measured_block"] >= 1, "grid recorded no measurement"


UI_READS = ("before", "after", "measured_block", "factor", "phase",
            "palette_size", "kept", "deferred", "warnings", "layers")


def test_the_report_carries_every_key_the_editor_reads(root, img):
    stack = [{"layer": k, "id": k, "enabled": True, "config": {}}
             for k in ("curves", "grid", "palette", "background", "scale")]
    _, facts = definitive.apply_stack(img, stack, palettes=_committed(root))
    missing = [k for k in UI_READS if k not in facts]
    assert not missing, f"web/js/views/editor reads {missing} and they are gone"
    assert not [la for la in facts["layers"] if la.get("error")]


def test_a_report_key_belongs_to_the_layer_that_declares_it():
    said = {r for spec in definitive.REGISTRY.values() for r in spec.reports}
    assert said == {"measured_block", "factor", "phase", "palette_size",
                    "kept", "colours"}


def _stack_of(*keys):
    return [{"layer": k, "id": k, "enabled": True, "config": {}} for k in keys]


@pytest.mark.parametrize("order,late", [
    (("palette", "grid"), "Grid"),
    (("background", "grid"), "Grid"),
])
def test_an_order_that_measures_destroyed_pixels_is_refused(order, late):
    # A palette measured at full resolution was applied to the reduced image.
    with pytest.raises(Invalid) as caught:
        definitive.validate_order(_stack_of(*order))
    assert late in caught.value.message
    assert caught.value.detail["field"] == order[0]
    assert caught.value.hint, "a refusal with no way forward is a dead end"


@pytest.mark.parametrize("order", [
    ("grid", "palette", "curves"),
    ("palette", "curves"),          # nothing reduces, so nothing is contradicted
    ("background", "curves"),
])
def test_a_consistent_order_is_not_refused(order):
    definitive.validate_order(_stack_of(*order))


@pytest.mark.parametrize("order,warns", [
    (("grid", "scale", "palette"), True),      # scale is not last
    (("grid", "grid", "palette"), True),       # grid twice
    (("grid", "palette", "scale"), False),
])
def test_an_order_that_only_costs_something_still_warns(order, warns):
    # Cost and uniqueness are not dependency rules, so they stay advisory.
    assert bool(definitive.check_order(_stack_of(*order))) is warns


def _committed(root):
    """The resolver `api` hands to apply_stack, built against a real library."""
    from pipeline.looks.palettes import registry

    return lambda name: Path(registry(root).get(name).path)


def test_a_failing_layer_reports_against_itself(root, img):
    broken = [{"layer": "palette", "id": "p", "enabled": True,
               "config": {"source": "file", "file": "nope"}}]
    out, facts = definitive.apply_stack(img, broken, palettes=_committed(root))
    assert out.shape == img.shape, "a failing layer changed the image"
    assert any(la.get("error") for la in facts["layers"])


def test_a_committed_palette_is_resolved_by_what_the_caller_supplied(tmp_path, img):
    # `root` is the real repo, so a test that writes a palette writes elsewhere.
    seen = []
    committed = tmp_path / "two.hex"
    committed.write_text("000000\nffffff\n")

    def resolve(name):
        seen.append(name)
        return committed

    stack = [{"layer": "palette", "id": "p", "enabled": True,
              "config": {"source": "file", "file": "two"}}]
    _, facts = definitive.apply_stack(img, stack, palettes=resolve)
    assert seen == ["two"]
    assert facts["palette_size"] == 2
    assert not [la for la in facts["layers"] if la.get("error")]


def test_reading_a_committed_palette_with_no_resolver_says_so(img):
    stack = [{"layer": "palette", "id": "p", "enabled": True,
              "config": {"source": "file", "file": "two"}}]
    _, facts = definitive.apply_stack(img, stack)
    assert "palette.file" in facts["layers"][0]["error"] or \
           "resolve" in facts["layers"][0]["error"]


def test_a_bad_hex_colour_is_a_message_not_a_defect():
    stack = [{"layer": "background", "id": "b0", "enabled": True,
              "config": {"enabled": True, "tolerance": 14, "colour": "#zzzzzz"}}]
    # A half-typed hex is a message against that layer, not a 500.
    _, facts = definitive.apply_stack(np.zeros((8, 8, 3), np.uint8), stack)
    errors = [layer.get("error", "") for layer in facts["layers"]]
    assert any("Invalid" in e for e in errors)


def test_a_cold_run_resumes_from_nowhere(root, img, stack):
    cache.SNAPSHOTS.clear()
    _, facts = definitive.apply_stack(img, stack, source="t")
    assert facts["resumed_after"] == 0


def test_a_change_at_the_end_does_not_recompute_the_start(root, img, stack):
    cache.SNAPSHOTS.clear()
    definitive.apply_stack(img, stack, source="t")
    tail = [dict(s, config=dict(s["config"])) for s in stack]
    tail[-1]["config"]["upscale"] = 3
    _, facts = definitive.apply_stack(img, tail, source="t")
    assert facts["resumed_after"] == len(stack) - 1


def test_a_change_at_the_start_invalidates_everything_after_it(root, img, stack):
    cache.SNAPSHOTS.clear()
    definitive.apply_stack(img, stack, source="t")
    head = [dict(s, config=dict(s["config"])) for s in stack]
    head[0]["config"]["contrast"] = 1.4
    _, facts = definitive.apply_stack(img, head, source="t")
    assert facts["resumed_after"] == 0


def test_resuming_produces_the_same_image_as_not_resuming(root, img, stack):
    cache.SNAPSHOTS.clear()
    definitive.apply_stack(img, stack, source="t")
    a, _ = definitive.apply_stack(img, stack, source="t")
    b, _ = definitive.apply_stack(img, stack)
    assert np.array_equal(a, b)


@pytest.mark.slow
def test_the_caches_are_bounded(root, img, stack):
    # One preview measured 6.96 s and 363 MB of peak RSS, one per change.
    for i in range(60):
        definitive.apply_stack((img + i).astype(np.uint8), stack,
                               source=f"t{i}")
    assert cache.SNAPSHOTS.stats()["bytes"] <= cache.SNAPSHOTS.max_bytes
    assert cache.CACHE.stats()["bytes"] <= cache.CACHE.max_bytes


def _reordered(stack, key):
    return ([e for e in stack if e["layer"] == key]
            + [e for e in stack if e["layer"] != key])


def test_every_registered_layer_is_budget_guarded():
    for key, spec in definitive.REGISTRY.items():
        assert getattr(spec.apply, "_budgeted", False), f"{key}.apply is unguarded"
        if spec.prepare is not None:
            assert getattr(spec.prepare, "_budgeted", False), f"{key}.prepare is unguarded"


def test_a_layer_built_without_the_decorator_is_guarded_too():
    spec = layers.LayerSpec(key="handmade", label="Handmade", summary="",
                            fields=[], apply=lambda img, cfg, facts, prep: img)
    assert getattr(spec.apply, "_budgeted", False)


def test_enlarging_before_an_analysing_layer_is_refused(root, img, stack):
    out, facts = definitive.apply_stack(img, _reordered(stack, "scale"))
    refused = [la for la in facts["layers"] if la.get("error")]
    assert refused, "an enlarged image reached a layer that analyses it"
    assert "MP from a" in refused[0]["error"], f"unhelpful refusal: {refused[0]['error']}"


def test_the_default_order_is_never_refused(root, img, stack):
    _, facts = definitive.apply_stack(img, stack)
    assert not [la for la in facts["layers"] if la.get("error")]


def test_scale_out_of_place_warns_before_it_refuses(stack):
    assert any("Scale is not last" in w
               for w in definitive.check_order(_reordered(stack, "scale")))
    assert not [w for w in definitive.check_order(stack) if "Scale" in w]


def test_the_budget_does_not_leak_between_runs(root, img, stack):
    definitive.apply_stack(img, _reordered(stack, "scale"))
    big = np.zeros((img.shape[0] * 3, img.shape[1] * 3, 3), dtype=np.uint8)
    _, facts = definitive.apply_stack(big, stack)
    assert not [la for la in facts["layers"] if la.get("error")], \
        "a previous run's budget refused a larger independent image"


def test_resume_reaches_start_run_as_resume(monkeypatch):
    """Resume was wired to the `stages` key, so it never arrived."""
    from pipeline.api import runs as runs_api

    seen = {}

    def fake(config_name, overrides, resume, style_picks=None):
        seen.update(config=config_name, resume=resume)
        return resume or "new"

    monkeypatch.setattr(runs_api, "start_run", fake)

    class Req:
        body = {"resume": "20260101_000000_x"}

        def get(self, key, default=None):
            return self.body.get(key, default)

    runs_api.Runs().start(Req())
    assert seen["resume"] == "20260101_000000_x"
    assert seen["config"] == ""


def test_a_second_run_is_refused_while_one_is_going(monkeypatch):
    """Two SDXL subprocesses compete for one GPU; two on one run corrupt it."""
    from pipeline.api import runs as runs_api
    from pipeline.shared.errors import Conflict

    monkeypatch.setattr(runs_api, "_in_flight", lambda: "20260101_000000_x")
    with pytest.raises(Conflict) as caught:
        runs_api.start_run("char_3", {}, None)
    assert "20260101_000000_x" in str(caught.value)


def test_resuming_the_run_that_is_already_running_is_refused(monkeypatch):
    from pipeline.api import runs as runs_api
    from pipeline.shared.errors import Conflict

    monkeypatch.setattr(runs_api, "_in_flight", lambda: "20260101_000000_x")
    with pytest.raises(Conflict):
        runs_api.start_run("", {}, "20260101_000000_x")


def test_nothing_running_lets_a_run_start(monkeypatch, tmp_path):
    from pipeline.api import runs as runs_api

    monkeypatch.setattr(runs_api, "_in_flight", lambda: None)
    started = {}

    class FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        started["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(runs_api.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runs_api, "runs_dir", lambda: tmp_path)
    (tmp_path / "20260101_000000_x").mkdir()

    run_id = runs_api.start_run("", {}, "20260101_000000_x")
    assert run_id == "20260101_000000_x"
    assert "--resume" in started["cmd"]
    runs_api._ACTIVE.pop(run_id, None)


def test_a_finished_run_does_not_block_the_next(monkeypatch):
    """A dead entry in _ACTIVE must be forgotten, not treated as in flight."""
    from pipeline.api import runs as runs_api

    class Dead:
        def poll(self):
            return 0

    monkeypatch.setattr(runs_api.guard, "run_in_flight", lambda: None)
    runs_api._ACTIVE["20260101_000000_done"] = Dead()
    try:
        assert runs_api._in_flight() is None
        assert "20260101_000000_done" not in runs_api._ACTIVE
    finally:
        runs_api._ACTIVE.pop("20260101_000000_done", None)
