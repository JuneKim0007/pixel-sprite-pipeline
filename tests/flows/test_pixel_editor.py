from __future__ import annotations

import numpy as np
import pytest

from pipeline import definitive
from pipeline.definitive import cache
from pipeline.shared import limits


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
    # A raising layer does not kill the run, which hides a broken import
    # perfectly: regrouping the package moved `training`, _grid_prepare kept
    # the old path, and the only symptom was one fact missing from a dict.
    out, facts = definitive.apply_stack(img, stack, root=root)
    assert out.ndim == 3, "the stack did not return an image"
    broke = [f"{la['layer']}: {la['error']}" for la in facts["layers"]
             if la.get("error")]
    assert not broke, f"the default stack cannot run: {broke}"
    assert not facts["warnings"], f"the default order warns: {facts['warnings']}"
    assert facts["measured_block"] >= 1, "grid recorded no measurement"


@pytest.mark.parametrize("order,warns", [
    (("palette", "grid"), True),
    (("background", "grid"), True),
    (("grid", "palette", "curves"), False),
])
def test_a_questionable_order_warns_rather_than_blocks(order, warns):
    built = [{"layer": k, "id": k, "enabled": True, "config": {}} for k in order]
    assert bool(definitive.check_order(built)) is warns


def test_a_failing_layer_reports_against_itself(root, img):
    broken = [{"layer": "palette", "id": "p", "enabled": True,
               "config": {"source": "file", "file": "nope"}}]
    out, facts = definitive.apply_stack(img, broken, root=root)
    assert out.shape == img.shape, "a failing layer changed the image"
    assert any(la.get("error") for la in facts["layers"])


def test_a_cold_run_resumes_from_nowhere(root, img, stack):
    cache.SNAPSHOTS.clear()
    _, facts = definitive.apply_stack(img, stack, root=root, source="t")
    assert facts["resumed_after"] == 0


def test_a_change_at_the_end_does_not_recompute_the_start(root, img, stack):
    cache.SNAPSHOTS.clear()
    definitive.apply_stack(img, stack, root=root, source="t")
    tail = [dict(s, config=dict(s["config"])) for s in stack]
    tail[-1]["config"]["upscale"] = 3
    _, facts = definitive.apply_stack(img, tail, root=root, source="t")
    assert facts["resumed_after"] == len(stack) - 1


def test_a_change_at_the_start_invalidates_everything_after_it(root, img, stack):
    cache.SNAPSHOTS.clear()
    definitive.apply_stack(img, stack, root=root, source="t")
    head = [dict(s, config=dict(s["config"])) for s in stack]
    head[0]["config"]["contrast"] = 1.4
    _, facts = definitive.apply_stack(img, head, root=root, source="t")
    assert facts["resumed_after"] == 0


def test_resuming_produces_the_same_image_as_not_resuming(root, img, stack):
    cache.SNAPSHOTS.clear()
    definitive.apply_stack(img, stack, root=root, source="t")
    a, _ = definitive.apply_stack(img, stack, root=root, source="t")
    b, _ = definitive.apply_stack(img, stack, root=root)
    assert np.array_equal(a, b)


@pytest.mark.slow
def test_the_caches_are_bounded(root, img, stack):
    # Without this they become the problem they solve: one preview measured
    # 6.96 s and 363 MB of peak RSS, one per parameter change, none serialised.
    for i in range(60):
        definitive.apply_stack((img + i).astype(np.uint8), stack, root=root,
                               source=f"t{i}")
    assert cache.SNAPSHOTS.stats()["bytes"] <= cache.SNAPSHOTS.max_bytes
    assert cache.CACHE.stats()["bytes"] <= cache.CACHE.max_bytes
