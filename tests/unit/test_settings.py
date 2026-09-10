from __future__ import annotations

from pipeline.shared import settings


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
