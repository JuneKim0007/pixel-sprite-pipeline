from __future__ import annotations

from pipeline.shared import settings
import pytest


def test_a_pipeline_value_beats_a_global_one():
    merged = settings.deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}})
    assert merged == {"a": {"b": 9, "c": 2}}, "merge lost a key"


def test_presence_is_the_override_not_difference():
    assert settings.overridden_paths({"a": {"b": 1}}) == {"a.b"}


def test_no_ui_flag_suppresses_a_destructive_confirmation():
    """A gate warning may be silenced; one that decides what happens to files may not.

    `suppress_overwrite_confirm` was declared and read by nothing. Wiring it was
    the obvious next step and the wrong one: the download dialog's answer is not
    a preference but a decision per download, and remembering "Overwrite" would
    clobber files silently from then on.
    """
    from pipeline.shared import settings

    ui = settings.DEFAULT_GLOBAL.get("ui", {})
    assert "suppress_overwrite_confirm" not in ui
    for key in ui:
        assert "overwrite" not in key, f"{key} silences a destructive answer"


class TestUnknownPaths:
    """A setting the schema never declared is a typo, and used to be silent."""

    def test_a_misspelled_path_is_refused_by_name(self):
        from pipeline.generation import schema
        from pipeline.shared.errors import Invalid

        with pytest.raises(Invalid) as caught:
            schema.SCHEMA.check({"canonical": {"style_weigth": 0.2}})
        assert "style_weigth" in str(caught.value)

    def test_it_suggests_the_path_that_was_meant(self):
        from pipeline.generation import schema
        from pipeline.shared.errors import Invalid

        with pytest.raises(Invalid) as caught:
            schema.SCHEMA.check({"pose": {"sorce": "tpose"}})
        assert "pose.source" in (caught.value.hint or "")

    def test_every_shipped_config_passes(self):
        """The check is only worth having if the configs it guards are valid."""
        from pathlib import Path

        from pipeline.generation import schema
        from pipeline.shared import settings

        for path in sorted(Path("library/configs").glob("*.yaml")):
            schema.SCHEMA.check(settings.read_yaml(path))

    def test_a_list_path_is_structure_not_a_typo(self):
        from pipeline.generation import schema

        schema.SCHEMA.check({"references": {"identity": [{"path": "x.png"}]},
                             "pose": {"set": [{"view": "front"}]},
                             "props": ["longsword"]})

    def test_unset_is_not_out_of_range(self):
        """`stop_after:` with nothing after it is None, and means "use the default"."""
        from pipeline.generation import schema

        schema.SCHEMA.check({"pipeline": {"stop_after": None},
                             "depth": {"size": None}})

    def test_a_per_group_mapping_is_checked_entry_by_entry(self):
        from pipeline.generation import schema
        from pipeline.shared.errors import Invalid

        schema.SCHEMA.check({"depth": {"build": {"torso": 1.6, "arms": 0.9}}})
        with pytest.raises(Invalid):
            schema.SCHEMA.check({"depth": {"build": {"torso": 99}}})

    def test_every_default_passes_its_own_check(self):
        """frames.ip_adapter.weight_type defaulted to a value its options refused."""
        from pipeline.generation import schema

        for field in schema.FIELDS:
            if field.default is not None:
                field.check(field.default)
