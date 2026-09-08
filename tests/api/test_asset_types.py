from __future__ import annotations

import pytest

from pipeline.shared import modules


@pytest.fixture
def made(root):
    """Type files this test wrote, removed afterwards — `root` is the real repo."""
    written = []
    yield written
    for key in written:
        (modules.directory(root) / f"{key}.yaml").unlink(missing_ok=True)


def test_availability_is_derived_from_the_stage_registry(http):
    """It used to be a boolean somebody set, which could say ready before it was."""
    found = http.get("/api/modules")["modules"]
    assert found["animation"]["available"] is True
    assert found["animation"]["missing"] == []
    assert found["tileset"]["available"] is False
    assert "tile_edges" in found["tileset"]["missing"], found["tileset"]


def test_a_type_may_be_saved_naming_a_stage_nobody_registered(http, made):
    """The type states the work; the code fills it in later."""
    made.append("weather")
    body = http.send("/api/module?name=weather", {"module": {
        "label": "Weather", "detail": "overlays", "blurb": "Rain and snow.",
        "stages": ["cloud_field", "canonical", "frames", "palette", "export"]}},
        "PUT")
    assert body["available"] is False
    assert body["missing"] == ["cloud_field"]
    assert http.get("/api/modules")["modules"]["weather"]["available"] is False


def test_an_order_that_could_never_run_is_refused(http, made):
    made.append("nope")
    code = http.status("/api/module?name=nope", {"module": {
        "label": "Nope", "detail": "d", "blurb": "b",
        "stages": ["frames", "pose"]}}, "PUT")
    assert code == 400, "an unrunnable order was accepted"


def test_a_key_that_is_not_a_filename_is_refused(http):
    assert http.status("/api/module?name=Not%20A%20Key", {"module": {
        "label": "x", "detail": "d", "blurb": "b", "stages": ["pose"]}},
        "PUT") == 400


def test_extends_must_name_something(http, made):
    made.append("orphan")
    assert http.status("/api/module?name=orphan", {"module": {
        "label": "x", "detail": "d", "blurb": "b", "stages": ["pose"],
        "extends": "no_such_type"}}, "PUT") == 400


def test_extending_a_type_widens_its_settings_form(http, made):
    """Without this a new type shows only the fields no type claims, which reads
    as the form having lost half its knobs."""
    made.append("portrait")
    http.send("/api/module?name=portrait", {"module": {
        "label": "Portraits", "detail": "faces", "blurb": "A head, several ways.",
        "extends": "character_sheet",
        "stages": ["pose", "canonical", "frames", "palette", "export"]}}, "PUT")

    sheet = {f["path"] for f in http.get("/api/schema?module=character_sheet")["fields"]}
    mine = {f["path"] for f in http.get("/api/schema?module=portrait")["fields"]}
    bare = {f["path"] for f in http.get("/api/schema?module=animation")["fields"]}
    assert "pose.symmetric" in sheet, "fixture assumption changed"
    assert sheet <= mine, sorted(sheet - mine)
    assert "pose.symmetric" not in bare
