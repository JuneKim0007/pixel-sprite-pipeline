from __future__ import annotations

import math

import pytest

from pipeline.geometry import bodyspace as bs
from pipeline.geometry import rigs
from pipeline.geometry.depthmap import render_depth
from pipeline.geometry.openpose import render

ALL = sorted(rigs.REGISTRY)
HUMANOIDS = ["humanoid", "humanoid_4arm", "humanoid_6arm", "humanoid_tailed"]


@pytest.mark.parametrize("name", ALL)
def test_declared_joints_cover_tree_and_bones(name):
    rig = rigs.REGISTRY[name]
    joints = set(rig.joints)
    for parent, kids in rig.tree.items():
        assert parent in joints, f"tree parent {parent} undeclared"
        assert not set(kids) - joints, f"tree children {set(kids) - joints} undeclared"
    assert not joints - set(rig.neutral), "neutral is missing declared joints"
    for a, b, _w in rig.bones:
        assert {a, b} <= joints, f"bone {a}->{b} undeclared"


@pytest.mark.parametrize("name", ALL)
def test_every_joint_is_reachable_from_root(name):
    rig = rigs.REGISTRY[name]
    seen, stack = {rig.root}, [rig.root]
    while stack:
        for kid in rig.tree.get(stack.pop(), ()):
            if kid not in seen:
                seen.add(kid)
                stack.append(kid)
    assert not set(rig.joints) - seen


@pytest.mark.parametrize("name", ALL)
def test_both_control_channels_render(name):
    rig = rigs.REGISTRY[name]
    kp = bs.project(rig.neutral, 40, rig=rig)
    assert len(kp) == len(rig.joints)
    if rig.skeleton_control:
        render(kp, 64, 64, rig=rig)
    render_depth(rig.neutral, 40, 64, 64, rig=rig)


def test_only_humanoid_claims_openpose():
    claimants = [n for n, r in rigs.REGISTRY.items()
                 if r.skeleton_control == "openpose"]
    assert claimants == ["humanoid"], "a rig claims a model that cannot read it"

    assert len(rigs.HUMANOID.joints) == 18, "OpenPose wants 18 joints"
    assert rigs.HUMANOID.joints[0] == "nose", "COCO order broken"


def test_rig_none_has_no_geometry():
    none = rigs.get("none")
    assert not none.joints
    assert none.skeleton_control is None
    assert none.depth_control is None


def _arm_degrees(p):
    return math.degrees(math.atan2(
        abs(p["l_wrist"][0] - p["l_shoulder"][0]),
        abs(p["l_wrist"][2] - p["l_shoulder"][2]) or 1e-9))


def test_reference_pose_clears_the_torso_without_going_horizontal():
    # ~40 degrees is the settled answer.
    rig = rigs.HUMANOID
    neutral = {k: list(v) for k, v in rig.neutral.items()}
    pose = rigs.tpose(rig)

    def clearance(p):
        return abs(p["l_wrist"][0]) / (abs(p["l_hip"][0]) or 1e-9)

    assert _arm_degrees(neutral) < 15, "the rig's neutral is no longer arms-down"
    assert 30 <= _arm_degrees(pose) <= 55, f"arm is {_arm_degrees(pose):.0f} degrees"
    assert clearance(pose) > 2 * clearance(neutral)
    assert _arm_degrees(rigs.tpose(rig, spread=88)) > 80, "spread override ignored"


@pytest.mark.parametrize("name", HUMANOIDS)
@pytest.mark.parametrize("symmetric", [False, True])
def test_tpose_rotation_is_rigid(name, symmetric):
    # A ray from the shoulder rescales the forearm.
    rig = rigs.get(name)
    posed = rigs.tpose(rig, symmetric=symmetric)
    for a, b, _w in rig.bones:
        if a not in rig.neutral or b not in rig.neutral:
            continue
        want = math.dist(rig.neutral[a][:3:2], rig.neutral[b][:3:2])
        if want <= 1e-9:
            continue
        got = math.dist(posed[a][:3:2], posed[b][:3:2])
        assert abs(want - got) < 1e-6, f"bone {a}->{b}: {want:.4f} -> {got:.4f}"


def _span(rig, a, b):
    return math.dist(rig.neutral[a], rig.neutral[b])


@pytest.mark.parametrize("a,b,factor", [("neck", "nose", 2.5),
                                        ("l_shoulder", "l_elbow", 1.4),
                                        ("l_hip", "l_knee", 0.8)])
def test_scale_applies_the_factor_to_its_group(a, b, factor):
    base = rigs.HUMANOID
    scaled = rigs.scale(base, {"neck": 2.5, "arms": 1.4, "legs": 0.8, "head": 1.5})
    assert abs(_span(scaled, a, b) - _span(base, a, b) * factor) < 1e-9


def test_scale_carries_the_head_radius():
    base = rigs.HUMANOID
    assert rigs.scale(base, {"head": 1.5}).head_radius > base.head_radius


@pytest.mark.parametrize("name", ["humanoid", "dragon", "spider", "serpent"])
def test_scale_leaves_unnamed_bones_alone(name):
    rig = rigs.get(name)
    moved = rigs.scale(rig, {"neck": 2.0, "legs": 0.6, "tail": 1.8})
    assert set(moved.neutral) == set(rig.neutral), "joints lost"
    for a, b, _w in rig.bones:
        if rigs.group_of(a, b) in {"neck", "legs", "tail"}:
            continue
        assert abs(_span(moved, a, b) - _span(rig, a, b)) < 1e-9, f"{a}->{b} moved"


@pytest.mark.parametrize("group,name,joint", [("legs", "humanoid", "l_ankle"),
                                              ("torso", "humanoid", "l_hip"),
                                              ("arms", "humanoid", "l_wrist"),
                                              ("neck", "humanoid", "nose"),
                                              ("tail", "quadruped", "tail_tip"),
                                              ("wings", "dragon", None)])
def test_every_proportion_group_moves_something(group, name, joint):
    rig = rigs.get(name)
    bigger = rigs.scale(rig, {group: 1.5})
    shifted = [j for j in rig.neutral
               if tuple(rig.neutral[j]) != tuple(bigger.neutral[j])]
    assert shifted if joint is None else joint in shifted


def test_thickness_follows_length_but_not_by_the_full_factor():
    base = rigs.HUMANOID
    thin = {(a, b): w for a, b, w in base.bones}
    thick = {(a, b): w for a, b, w in rigs.scale(base, {"legs": 1.75}).bones}
    leg, arm = ("l_hip", "l_knee"), ("l_shoulder", "l_elbow")
    assert thin[leg] < thick[leg] < thin[leg] * 1.75, "taller is not wider"
    assert thick[arm] == thin[arm], "scaling legs changed arm thickness"


def test_unknown_proportion_group_is_rejected():
    from pipeline.shared.errors import Invalid

    with pytest.raises(Invalid) as caught:
        rigs.scale(rigs.HUMANOID, {"elbows": 2.0})
    assert caught.value.status == 400


def test_an_unknown_view_names_the_ones_that_exist():
    from pipeline.shared.errors import NotFound

    with pytest.raises(NotFound) as caught:
        bs.resolve_view("sideways")
    assert caught.value.status == 404
    assert "front" in caught.value.hint


class TestProportions:
    """Bone groups scale, both sides together, everything below carried along."""

    @staticmethod
    def _chain(neutral, *joints):
        import math

        return sum(math.dist(neutral[a], neutral[b])
                   for a, b in zip(joints, joints[1:]))

    def test_a_group_scales_by_exactly_its_factor(self):
        from pipeline.geometry import rigs

        rig = rigs.get("humanoid")
        base = self._chain(rig.neutral, "l_hip", "l_knee", "l_ankle")
        for factor in (0.7, 1.4, 2.0):
            grown = rigs.scale(rig, {"legs": factor})
            got = self._chain(grown.neutral, "l_hip", "l_knee", "l_ankle")
            assert got == pytest.approx(base * factor, rel=1e-6)

    def test_both_sides_move_together(self):
        """A rig with one long leg is a mistake far more often than an intention."""
        import math

        from pipeline.geometry import rigs

        grown = rigs.scale(rigs.get("humanoid"), {"legs": 1.4})
        left = math.dist(grown.neutral["l_hip"], grown.neutral["l_knee"])
        right = math.dist(grown.neutral["r_hip"], grown.neutral["r_knee"])
        assert left == pytest.approx(right, abs=1e-9)

    def test_what_hangs_below_is_carried(self):
        from pipeline.geometry import rigs

        rig = rigs.get("humanoid")
        grown = rigs.scale(rig, {"legs": 1.4})
        assert grown.neutral["l_ankle"][2] != pytest.approx(rig.neutral["l_ankle"][2])

    def test_another_group_is_left_alone(self):
        from pipeline.geometry import rigs

        rig = rigs.get("humanoid")
        grown = rigs.scale(rig, {"legs": 1.4})
        before = self._chain(rig.neutral, "l_shoulder", "l_elbow", "l_wrist")
        after = self._chain(grown.neutral, "l_shoulder", "l_elbow", "l_wrist")
        assert after == pytest.approx(before, rel=1e-6)

    def test_no_factors_returns_the_same_rig(self):
        from pipeline.geometry import rigs

        rig = rigs.get("humanoid")
        assert rigs.scale(rig, None) is rig
        assert rigs.scale(rig, {}) is rig

    def test_an_unknown_group_is_refused_by_name(self):
        from pipeline.geometry import rigs
        from pipeline.shared.errors import Invalid

        with pytest.raises(Invalid) as caught:
            rigs.scale(rigs.get("humanoid"), {"elbows": 1.2})
        assert "elbows" in str(caught.value)


class TestWeightMap:
    """A painted emphasis map, stored beside the image it describes."""

    def test_it_round_trips_through_the_sidecar(self, tmp_path):
        import numpy as np
        from PIL import Image

        from pipeline.geometry import weightmap as wm

        image = tmp_path / "ref.png"
        Image.new("RGB", (8, 8)).save(image)

        painted = wm.radial(0.95, 0.60)
        wm.save(image, painted)
        back = wm.load(image)

        assert back is not None
        assert back.shape == painted.shape
        # 8-bit storage, so a value is kept to within half a step.
        assert np.abs(back - painted).max() <= 1 / 255 + 1e-6

    def test_an_unpainted_image_has_no_map(self, tmp_path):
        from PIL import Image

        from pipeline.geometry import weightmap as wm

        image = tmp_path / "ref.png"
        Image.new("RGB", (8, 8)).save(image)
        assert wm.load(image) is None
        assert wm.clear(image) is False

    def test_the_sidecar_sits_beside_the_image(self, tmp_path):
        from pipeline.geometry import weightmap as wm

        assert wm.sidecar_for(tmp_path / "a.png").name == "a.png.weight.png"

    def test_radial_is_strongest_in_the_middle(self):
        from pipeline.geometry import weightmap as wm

        grid = wm.radial(0.9, 0.8)
        middle = wm.EDGE // 2
        assert grid[middle, middle] > grid[0, 0]
        assert grid[0, 0] == pytest.approx(0.8, abs=1e-3)

    def test_flat_is_one_value_everywhere(self):
        from pipeline.geometry import weightmap as wm

        grid = wm.flat(0.8)
        assert grid.min() == pytest.approx(grid.max())

    def test_values_outside_the_range_are_clipped(self, tmp_path):
        import numpy as np
        from PIL import Image

        from pipeline.geometry import weightmap as wm

        image = tmp_path / "ref.png"
        Image.new("RGB", (8, 8)).save(image)
        wm.save(image, np.array([[-4.0, 9.0], [0.5, 0.5]], dtype=np.float32))
        back = wm.load(image)
        assert back.min() >= 0.0 and back.max() <= 1.0

    def test_it_is_stored_at_the_grid_the_sampler_uses(self):
        """samplers.py resizes a mask to the latent grid, an eighth of the image."""
        from pipeline.geometry import weightmap as wm

        assert wm.EDGE == 128
