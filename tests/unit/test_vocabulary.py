from __future__ import annotations

import pytest

from pipeline.looks import vocabulary as v


@pytest.mark.parametrize("word", ["skeleton", "undead", "stick figure"])
def test_the_pose_guard_names_what_it_is_guarding_against(word):
    assert word in v.POSE_NEGATIVE


def test_a_pose_control_image_gets_an_anti_tracing_negative():
    assert v.POSE_NEGATIVE in v.negative_for("base", pose_control=True)


def test_the_guard_is_absent_with_no_control_image_to_justify_it():
    assert v.POSE_NEGATIVE not in v.negative_for("base", pose_control=False)


def test_the_guard_can_be_turned_off():
    assert v.POSE_NEGATIVE not in v.negative_for("base", pose_control=True,
                                                 guard_skeletons=False)


def test_a_keyed_backdrop_gets_its_negative():
    assert v.BACKDROP_NEGATIVE in v.negative_for("base", backdrop=True)


@pytest.mark.parametrize("yaw,word", [(0, "front"), (180, "rear")])
def test_a_yaw_reads_as_a_view(yaw, word):
    assert word in v.view_words(yaw)


class TestBackdropConflict:
    """A style that describes a background argues with the backdrop clause."""

    def test_it_names_the_phrase_that_clashes(self):
        from pipeline.looks import vocabulary

        assert vocabulary.backdrop_conflict(
            "pixel art, plain flat background", "#FF00FF") == "flat background"

    def test_a_clean_style_is_quiet(self):
        from pipeline.looks import vocabulary

        assert vocabulary.backdrop_conflict("pixel art, game sprite", "#FF00FF") == ""

    def test_no_backdrop_means_no_conflict(self):
        """Without a chroma key, describing a background is the only instruction."""
        from pipeline.looks import vocabulary

        assert vocabulary.backdrop_conflict("plain flat background", None) == ""

    def test_the_default_style_does_not_argue_with_the_default_backdrop(self):
        from pipeline.looks import vocabulary

        assert vocabulary.backdrop_conflict(
            vocabulary.DEFAULT_STYLE, vocabulary.BACKDROP) == ""

    def test_no_shipped_config_asks_for_two_backgrounds(self):
        from pathlib import Path

        from pipeline.looks import vocabulary
        from pipeline.shared import settings

        for path in sorted(Path("library/configs").glob("*.yaml")):
            if path.stem == "_global":
                continue
            cfg = settings.read_yaml(path)
            backdrop = vocabulary.backdrop_colour(cfg.get("background") or None)
            clash = vocabulary.backdrop_conflict(
                cfg.get("style") or vocabulary.DEFAULT_STYLE, backdrop)
            assert clash == "", f"{path.stem}: style says '{clash}'"
