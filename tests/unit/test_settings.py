from __future__ import annotations

from pipeline.shared import settings


def test_a_pipeline_value_beats_a_global_one():
    merged = settings.deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}})
    assert merged == {"a": {"b": 9, "c": 2}}, "merge lost a key"


def test_presence_is_the_override_not_difference():
    assert settings.overridden_paths({"a": {"b": 1}}) == {"a.b"}
