"""Cutting a character sheet into one image per view."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

_spec = importlib.util.spec_from_file_location(
    "cut_sheet", Path(__file__).resolve().parents[2] / "tools/cut_sheet.py")
cut_sheet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cut_sheet)


def sheet(tmp_path, figures=4, panel=False, bg=(240, 236, 230)):
    """A sheet: tall figures on a shared baseline, optionally a panel beside."""
    w, h = 1200, 800
    a = np.full((h, w, 3), bg, dtype=np.uint8)
    step = 240
    for i in range(figures):
        x = 60 + i * step
        a[80:720, x:x + 120] = (40, 40, 60)
    if panel:
        for row in range(4):
            top = 100 + row * 150
            a[top:top + 110, 1020:1160] = (90, 60, 60)
    path = tmp_path / "sheet.png"
    Image.fromarray(a).save(path)
    return path


def test_four_views_are_named_in_reading_order(tmp_path):
    out = tmp_path / "out"
    cut_sheet.cut(sheet(tmp_path), out)
    assert sorted(p.name for p in out.glob("*.png")) == [
        "_source_sheet.png", "front.png", "rear.png", "side.png", "side_right.png"]


def test_a_detail_panel_is_not_taken_for_a_figure(tmp_path):
    """The panel is the same colour and beside them; it is simply shorter."""
    out = tmp_path / "out"
    cut_sheet.cut(sheet(tmp_path, panel=True), out)
    widths = {p.stem: Image.open(p).size[0] for p in out.glob("*.png")
              if p.stem != "_source_sheet"}
    assert max(widths.values()) < 260, f"a crop swallowed the panel: {widths}"


def test_one_side_drawing_answers_for_both(tmp_path):
    """Three views is a real sheet, not an error, and the run needs four."""
    out = tmp_path / "out"
    cut_sheet.cut(sheet(tmp_path, figures=3), out, views=3)
    assert (out / "side_right.png").exists()
    assert Image.open(out / "side.png").tobytes() == \
        Image.open(out / "side_right.png").tobytes()


def test_the_source_sheet_is_kept_beside_the_cuts(tmp_path):
    out = tmp_path / "out"
    cut_sheet.cut(sheet(tmp_path), out)
    assert (out / "_source_sheet.png").exists()


def test_a_sheet_with_no_figures_is_refused(tmp_path):
    path = tmp_path / "flat.png"
    Image.fromarray(np.full((400, 400, 3), 200, dtype=np.uint8)).save(path)
    with pytest.raises(SystemExit):
        cut_sheet.cut(path, tmp_path / "out")


def test_a_column_far_wider_than_its_siblings_is_split(tmp_path):
    reach = np.zeros(1000)
    reach[100:200] = 1.0
    reach[400:500] = 1.0
    reach[700:1000] = 1.0
    reach[850:860] = 0.0
    picked = [(100, 200), (400, 500), (700, 1000)]
    out = cut_sheet._resplit(reach, picked)
    assert out[2][1] - out[2][0] < 200, "the merged column kept its panel"
