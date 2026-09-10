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
