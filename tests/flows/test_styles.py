from __future__ import annotations

import pytest

from pipeline.generation import resources
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
    merged, _ = styles.layer(root, {"module": "animation", "subject": "a knight",
                                    "styles": [sheet],
                                    "palette": {"source": "extract"}})
    assert merged["palette"]["source"] == "extract"


def test_a_missing_sheet_raises_not_found_with_alternatives(root):
    with pytest.raises(NotFound) as caught:
        styles.layer(root, {"styles": ["definitely_not_a_style"]})
    assert "base_pixel" in caught.value.hint


def test_naming_an_unknown_history_kind_lists_the_ones_that_do(tmp_path):
    from pipeline.looks import stylelog

    with pytest.raises(NotFound) as caught:
        stylelog.append(tmp_path, stylelog.Event(kind="not_a_kind"))
    assert caught.value.status == 404
    assert "context" in caught.value.hint


def test_source_annotation_refuses_when_no_annotation_was_made(root):
    from pipeline.shared.errors import Invalid

    ctx = Context(root=root, outdir=root,
                  config={"rig": "humanoid", "annotate": "skip",
                          "pose": {"source": "annotation"}})
    with pytest.raises(Invalid, match="(?i)annotat"):
        PoseStage()._resolve(ctx, ctx.settings("pose"), wanted=1)


def test_annotate_skip_measures_nothing(root):
    ctx = Context(root=root, outdir=root, config={"annotate": "skip"})
    assert resources.measured_proportions(ctx) == {}


class TestEditingASheet:
    """The route that saves a sheet's own words, which nothing drove until now."""

    @pytest.fixture
    def sandbox(self, tmp_path, monkeypatch):
        from pipeline.api import looks

        home = tmp_path / "library" / "styles" / "a_look"
        (home / "context").mkdir(parents=True)
        (home / "style.yaml").write_text(
            "name: a_look\nlabel: A look\nnotes: the sheet's own half\n"
            "vocabulary:\n  style: [bold outlines]\n")
        (home / "context" / "notes.md").write_text("the sidecar half\n")
        monkeypatch.setattr(looks, "ROOT", tmp_path)
        return looks, home

    def test_notes_read_back_as_they_were_saved(self, sandbox):
        looks, home = sandbox
        before = looks.style_detail("a_look")["context"]["prompts"]["notes"]
        assert "the sidecar half" in before

        looks.style_prompts("a_look", None, before)
        after = looks.style_detail("a_look")["context"]["prompts"]["notes"]
        assert after == before, "saving the notes shown doubled them on the way back"

    def test_the_sheet_keeps_its_prose_in_one_place(self, sandbox):
        looks, home = sandbox
        looks.style_prompts("a_look", None, "only this")
        assert "only this" in (home / "context" / "notes.md").read_text()
        assert "notes:" not in (home / "style.yaml").read_text()

    def test_a_fragment_survives_the_round_trip(self, sandbox):
        looks, _ = sandbox
        looks.style_prompts("a_look", {"mood": ["heroic", "grim"], "empty": []}, None)
        back = looks.style_detail("a_look")["context"]["prompts"]["vocabulary"]
        assert back["mood"] == ["heroic", "grim"]
        assert "empty" not in back, "a group with no words was written anyway"

    def test_a_list_is_required(self, sandbox):
        from pipeline.shared.errors import Invalid

        looks, _ = sandbox
        with pytest.raises(Invalid):
            looks.style_prompts("a_look", {"mood": "heroic"}, None)
