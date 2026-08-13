from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from pipeline.geometry import depthmap, props, rigs

RIG = rigs.HUMANOID
REST = {k: list(v) for k, v in RIG.neutral.items()}
RAISED = {**REST, "l_elbow": [0.12, 0.10, 0.28], "l_wrist": [0.16, 0.18, 0.18]}


@pytest.fixture
def sword():
    return props.load([{"name": "sword", "socket": "l_wrist", "length": 0.3,
                        "aim": [0, 0.4, -0.9], "prompt": "holding a longsword"}])


def test_prop_follows_the_limb_it_is_held_by(sword):
    a, b = props.tip(sword[0], REST, RIG), props.tip(sword[0], RAISED, RIG)
    assert a is not None and b is not None, "the prop produced no tip"
    assert math.dist(a, b) > 0.1, "the prop did not move with the arm"


@pytest.mark.parametrize("pose_name", ["rest", "raised"])
def test_prop_length_is_a_property_of_the_object_not_the_pose(sword, pose_name):
    pose = REST if pose_name == "rest" else RAISED
    grip = props.anchor(sword[0], pose, RIG)
    tip = props.tip(sword[0], pose, RIG)
    assert abs(math.dist(grip, tip) - sword[0].length) < 1e-6


def test_two_handed_prop_pulls_the_off_hand_to_the_grip():
    two = props.load([{"name": "gs", "socket": "l_wrist",
                       "second_socket": "r_wrist", "length": 0.4}])
    moved = props.pull_second_hand(two, REST, RIG)
    assert math.dist(REST["r_wrist"], moved["r_wrist"]) > 0.05
    assert REST["l_wrist"] == moved["l_wrist"], "the primary hand moved"


def test_prop_reaches_the_depth_map_and_the_prompt(sword):
    tpose = {k: list(v) for k, v in rigs.tpose(RIG).items()}
    bare = np.asarray(depthmap.render_depth(tpose, 0.0, 96, 96, rig=RIG, props=[]))
    armed = np.asarray(depthmap.render_depth(tpose, 0.0, 96, 96, rig=RIG, props=sword))
    assert not np.array_equal(bare, armed), "the prop drew nothing"
    assert "longsword" in props.prompt_terms(sword)


def test_prop_never_draws_below_the_body_floor():
    # Black is background, so a prop reaching it reads as a hole in the sprite.
    cape = props.load([{"name": "cape", "socket": "neck", "width": 0.15,
                        "length": 0.35, "flex": 0.3, "shade": 0.2}])
    raw = np.asarray(depthmap.render_depth(RIG.neutral, 40, 256, 256, rig=RIG,
                                           props=cape, blur=0))
    ink = raw[raw > 0]
    assert int(ink.min()) >= 60, f"a prop drew at {int(ink.min())}"


@pytest.mark.parametrize("config,wanted", [
    ({"module": "character_sheet"}, False),
    ({"module": "animation"}, True),
    ({"module": "character_sheet", "props": {"enabled": True}}, True),
    ({"module": "animation", "props": {"enabled": False}}, False),
])
def test_module_decides_whether_props_are_drawn(config, wanted):
    assert props.wanted(SimpleNamespace(config=config)) is wanted


def test_both_config_shapes_load(root):
    # The mapping form carries the enabled switch and used to fail with
    # "no prop 'enabled' in the library".
    flat = props.load(["bow"], root=root)
    mapped = props.load({"enabled": False, "items": ["bow"]}, root=root)
    assert [p.name for p in flat] == [p.name for p in mapped] == ["bow"]
