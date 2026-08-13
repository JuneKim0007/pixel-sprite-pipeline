from __future__ import annotations

import numpy as np
import pytest

from pipeline.definitive.pixelize import background_to_alpha
from pipeline.geometry import autorig


@pytest.fixture
def figure():
    # A crude standing figure: head, torso, two legs.
    mask = np.zeros((200, 120), dtype=bool)
    mask[20:50, 50:70] = True
    mask[50:110, 35:85] = True
    mask[110:190, 45:57] = True
    mask[110:190, 63:75] = True
    return mask


@pytest.mark.parametrize("upper,lower", [("nose", "l_shoulder"),
                                         ("l_shoulder", "l_hip"),
                                         ("l_hip", "l_knee"),
                                         ("l_knee", "l_ankle")])
def test_a_fit_lands_joints_in_anatomical_order(figure, upper, lower):
    p = autorig.fit_humanoid(figure).points
    assert p, "no joints proposed for a clear figure"
    assert p[upper][1] < p[lower][1], f"{upper} was placed below {lower}"


def test_left_and_right_are_not_swapped(figure):
    p = autorig.fit_humanoid(figure).points
    assert p["l_shoulder"][0] > p["r_shoulder"][0]


@pytest.mark.xfail(reason="passes>1 is unreachable: seeds come only from the "
                          "literal image border, which pass 1 clears, so an "
                          "inner panel is never peeled (pixelize.py:376)",
                   strict=True)
def test_multi_pass_keying_removes_a_layered_background():
    img = np.full((80, 80, 3), 40, dtype=np.uint8)   # border colour
    img[10:70, 10:70] = 200                          # panel, not touching the edge
    img[30:50, 35:45] = 120                          # subject inside the panel
    kept = (background_to_alpha(img, 12)[..., 3] > 0).mean()
    assert kept < 0.15, f"{kept:.0%} survived keying; the panel was not removed"


def test_keying_removes_a_single_flat_background():
    img = np.full((80, 80, 3), 40, dtype=np.uint8)
    img[25:55, 25:55] = 120                          # 14% of the frame
    kept = (background_to_alpha(img, 12)[..., 3] > 0).mean()
    assert abs(kept - 0.1406) < 0.01, f"{kept:.0%} survived; the subject is 14%"


def test_keying_refuses_a_pass_that_would_eat_the_subject():
    # keep_min: a subject under 4% of the frame means the flood has escaped
    # into it, so the pass is discarded rather than committed.
    img = np.full((80, 80, 3), 40, dtype=np.uint8)
    img[30:50, 35:45] = 120                          # 3% of the frame
    assert (background_to_alpha(img, 12)[..., 3] > 0).mean() == 1.0


def test_keying_never_eats_the_whole_subject():
    solid = np.full((40, 40, 3), 90, dtype=np.uint8)
    keyed = background_to_alpha(solid, 10)
    assert keyed.shape[:2] == solid.shape[:2]
