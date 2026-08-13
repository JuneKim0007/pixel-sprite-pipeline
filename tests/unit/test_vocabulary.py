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
