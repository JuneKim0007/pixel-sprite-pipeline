from __future__ import annotations

import pytest

from pipeline.generation.stage import Context
from pipeline.looks import styles
from pipeline.shared import NotFound
from pipeline.stages.pose import PoseStage


@pytest.fixture(scope="module")
def sheet(root):
    found = styles.discover(root)
    if not found:
        pytest.skip("no style sheets shipped")
    return "pokemon_mono" if "pokemon_mono" in found else next(iter(found))


def test_a_sheet_contributes_a_resolved_prompt(root, sheet):
    merged, record = styles.layer(
        root, {"module": "animation", "subject": "a knight", "styles": [sheet]})
    assert record["styles"], "no style was applied"
    assert "style" in merged, "the sheet contributed no prompt"
    assert "{" not in str(merged.get("style", "")), "a placeholder was left unresolved"


def test_a_pipeline_value_beats_a_style_sheet(root, sheet):
    # The layering order is the contract.
    merged, _ = styles.layer(root, {"module": "animation", "subject": "a knight",
                                    "styles": [sheet],
                                    "palette": {"source": "extract"}})
    assert merged["palette"]["source"] == "extract"


def test_a_missing_sheet_raises_not_found_with_alternatives(root):
    # NotFound, not StyleError: it carries what someone who mistyped needs.
    with pytest.raises(NotFound) as caught:
        styles.layer(root, {"styles": ["definitely_not_a_style"]})
    assert "base_pixel" in caught.value.hint


def test_source_annotation_refuses_when_no_annotation_was_made(root):
    ctx = Context(root=root, outdir=root,
                  config={"rig": "humanoid", "annotate": "skip",
                          "pose": {"source": "annotation"}})
    with pytest.raises(ValueError, match="(?i)annotat"):
        PoseStage()._resolve(ctx, ctx.stage_config("pose"), wanted=1)


def test_annotate_skip_measures_nothing(root):
    ctx = Context(root=root, outdir=root, config={"annotate": "skip"})
    assert ctx._measured_proportions() == {}
