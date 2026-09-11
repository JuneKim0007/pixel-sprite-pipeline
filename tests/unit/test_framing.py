"""Where the subject sits in its frame, measured rather than asked for."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from pipeline.geometry import framing


def frame(tmp_path, box, size=200, bg=(240, 90, 150), fg=(20, 20, 20)):
    a = np.full((size, size, 3), bg, dtype=np.uint8)
    left, top, right, bottom = box
    a[top:bottom, left:right] = fg
    path = tmp_path / "frame.png"
    Image.fromarray(a).save(path)
    return path


def test_an_empty_frame_measures_as_nothing(tmp_path):
    path = tmp_path / "flat.png"
    Image.fromarray(np.full((64, 64, 3), 200, dtype=np.uint8)).save(path)
    assert framing.measure(path) is None


def test_the_backdrop_is_read_from_the_corners_not_assumed(tmp_path):
    box = framing.measure(frame(tmp_path, (80, 80, 120, 120), bg=(12, 200, 60)))
    assert box.backdrop == (12, 200, 60)


def test_a_centred_subject_reports_its_share_of_the_frame(tmp_path):
    box = framing.measure(frame(tmp_path, (50, 50, 150, 150), size=200))
    assert box.clipped == []
    assert box.fill == pytest.approx(0.5, abs=0.02)


def test_a_subject_touching_an_edge_names_that_edge(tmp_path):
    box = framing.measure(frame(tmp_path, (50, 0, 150, 200), size=200))
    assert set(box.clipped) == {"top", "bottom"}


def test_a_clipped_subject_is_not_shifted(tmp_path):
    """Moving it would move the cut rather than undo it."""
    path = frame(tmp_path, (50, 0, 150, 200), size=200)
    before = Image.open(path).tobytes()
    assert framing.recentre(path) is None
    assert Image.open(path).tobytes() == before


def test_an_off_centre_subject_is_brought_back(tmp_path):
    path = frame(tmp_path, (10, 10, 60, 120), size=200)
    assert framing.recentre(path) is not None
    box = framing.measure(path)
    assert abs(box.left - (box.width - box.box_width - box.left)) <= 2
    assert abs(box.top - (box.height - box.box_height - box.top)) <= 2


def test_a_centred_subject_is_left_alone(tmp_path):
    path = frame(tmp_path, (50, 50, 150, 150), size=200)
    assert framing.recentre(path) is None


def test_the_backdrop_fills_what_the_shift_vacates(tmp_path):
    path = frame(tmp_path, (10, 10, 60, 120), size=200, bg=(240, 90, 150))
    framing.recentre(path)
    a = np.asarray(Image.open(path).convert("RGB"))
    assert tuple(a[0, 0]) == (240, 90, 150), "a shift left a border of some other colour"


def painted(tmp_path, flat=False):
    """A reference carrying a map that is NOT its own silhouette."""
    from pipeline.geometry import weightmap

    path = frame(tmp_path, (60, 40, 140, 170))
    weightmap.save(path, weightmap.flat(0.9) if flat
                   else weightmap.radial(0.95, 0.15))
    return path


def test_each_regional_source_does_something_different(tmp_path):
    """Four options shipped with three behaviours: `auto` and `painted` both
    fell through to the same return."""
    from pipeline.geometry import weightmap

    path = painted(tmp_path)
    got = {s: weightmap.resolve(path, s, 0.0, 1.0)
           for s in ("none", "auto", "subject")}
    import numpy as np

    assert got["none"] is None
    assert got["auto"] is not None and got["subject"] is not None
    # Means can coincide; the maps themselves are the claim.
    assert not np.allclose(got["auto"], got["subject"], atol=1e-3)


def test_auto_is_not_a_map_when_none_was_drawn(tmp_path):
    from pipeline.geometry import weightmap

    assert weightmap.resolve(frame(tmp_path, (60, 40, 140, 170)),
                             "auto", 0.0, 1.0) is None


def test_the_range_applies_whatever_drew_the_map(tmp_path):
    """floor and ceiling reached only `subject`, while ceiling's help said it
    softens a reference - which is the case a person reaches for."""
    import pytest

    from pipeline.geometry import weightmap

    for source in ("auto", "subject"):
        got = weightmap.resolve(painted(tmp_path), source, 0.25, 0.75)
        assert got.min() == pytest.approx(0.25, abs=1e-3), source
        assert got.max() == pytest.approx(0.75, abs=1e-3), source


def test_a_map_with_no_range_lands_in_the_middle_of_the_one_asked_for(tmp_path):
    """One value everywhere cannot be stretched to two, and picking an end
    would make `floor` and `ceiling` mean different things for a flat map."""
    import pytest

    from pipeline.geometry import weightmap

    got = weightmap.resolve(painted(tmp_path, flat=True), "auto", 0.25, 0.75)
    assert got.min() == got.max() == pytest.approx(0.5)
