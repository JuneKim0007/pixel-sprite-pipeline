"""Making a figure broad without making its face broad."""

from __future__ import annotations

import pytest

from pipeline.geometry import bodyspace as bs
from pipeline.geometry import rigs

H = rigs.HUMANOID


def widths(spread=None, lateral=1.0):
    points = bs.project(H.neutral, 0, fill=0.0, lateral_scale=lateral,
                        spread=spread, rig=H)
    by = {j: p for j, p in zip(H.all_joints, points) if p}
    heights = [p[1] for p in points if p]
    shoulder = abs(by["l_shoulder"][0] - by["r_shoulder"][0])
    ear = abs(by["l_ear"][0] - by["r_ear"][0])
    return {"tall": (max(heights) - min(heights)) / shoulder,
            "face": ear / shoulder, "ear": ear, "shoulder": shoulder}


def test_unset_spread_changes_nothing():
    assert widths() == widths(spread=1.0)


def test_lateral_scale_widens_the_face_with_the_body():
    """The existing lever is a uniform horizontal scale, which is why it alone
    could not answer 'broad shouldered'."""
    plain, wide = widths(), widths(lateral=1.4)
    assert wide["tall"] < plain["tall"], "the figure did not get broader"
    assert wide["face"] == pytest.approx(plain["face"]), \
        "lateral_scale stopped being uniform"


def test_a_group_spread_broadens_the_body_and_leaves_the_face():
    plain = widths()
    built = widths(spread={"arms": 1.4, "torso": 1.4})
    assert built["tall"] == pytest.approx(widths(lateral=1.4)["tall"], abs=0.01), \
        "a per-group spread should reach the same build"
    assert built["ear"] == pytest.approx(plain["ear"]), "the face widened too"
    assert built["face"] < plain["face"] * 0.8


def test_a_group_nobody_named_is_left_alone():
    """The shoulders ARE the arms group, so ear-over-shoulder moves even when
    the ear does not. The ear itself is the thing that must not move."""
    built = widths(spread={"arms": 1.4})
    assert built["ear"] == pytest.approx(widths()["ear"])
    assert built["shoulder"] > widths()["shoulder"]


def test_the_skeleton_and_the_depth_map_are_given_the_same_spread():
    """They are two channels of one pose; a disagreement draws two bodies."""
    import inspect

    from pipeline.geometry.depthmap import render_depth

    assert "spread" in inspect.signature(render_depth).parameters
    assert "spread" in inspect.signature(bs.project).parameters
