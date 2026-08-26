from __future__ import annotations

import pytest

from pipeline import api, definitive
from pipeline.generation.schema import FIELDS
from pipeline.shared import paths
from pipeline.shared.contracts import Field

SHAPES = {
    "/api/config": ("?name=char_1", ["name", "module", "raw", "config",
                                     "effective", "style_record", "overrides"]),
    "/api/schema": ("", ["fields", "options", "modules"]),
    "/api/system": ("", ["services", "paths", "host", "weights"]),
    "/api/configs": ("", ["configs"]),
    "/api/runs": ("", ["runs"]),
    "/api/styles": ("", ["styles"]),
    "/api/palettes": ("", ["palettes"]),
    "/api/queue": ("", ["states", "counts", "autopilot", "services"]),
    "/api/editor/layers": ("", ["layers", "default_stack"]),
    "/api/global": ("", ["config"]),
    "/api/poses": ("", ["library"]),
    "/api/rigpose": ("?rig=humanoid", ["rig", "joints", "tree", "bones",
                                       "neutral", "root", "limbs", "pose"]),
    "/api/style/detail": ("?name=retro_jrpg", []),
    "/api/style/preview": ("?config=character_sheet", []),
    "/api/style/training": ("?name=retro_jrpg", []),
    "/api/browse": ("", []),
}
NEEDS_ARG = {"/api/annotation", "/api/autorig", "/api/file", "/api/run"}
GETS = sorted(r["path"] for r in api.table.surface() if r["method"] == "GET")


@pytest.mark.parametrize("path", GETS)
def test_every_declared_get_route_answers(http, path):
    query, _keys = SHAPES.get(path, ("", []))
    code = http.status(path + query)
    assert code < 500, f"{path} fails server-side with {code}"
    if path not in NEEDS_ARG:
        assert code == 200, f"{path} answered {code}"


@pytest.mark.parametrize("path", sorted(p for p in SHAPES if SHAPES[p][1]))
def test_a_response_carries_the_fields_the_ui_reads(http, path):
    # /api/config returned four of seven keys once and every route still answered 200; the only symptom was the whole UI failing to start.
    query, keys = SHAPES[path]
    body = http.get(path + query)
    assert not [k for k in keys if k not in body], \
        f"missing {[k for k in keys if k not in body]}; has {sorted(body)}"


def test_every_select_has_options_to_offer(http):
    schema = http.get("/api/schema")
    empty = [f["path"] for f in schema["fields"]
             if f.get("options_from") and not schema["options"].get(f["options_from"])]
    assert not empty


def test_the_rig_list_is_not_truncated(http):
    rigs = set(http.get("/api/schema")["options"]["rigs"])
    assert {"humanoid", "dragon", "spider"} <= rigs


def test_the_palette_picker_is_filled_from_disk(http):
    fields = {f["key"]: f for s in http.get("/api/editor/layers")["layers"]
              if s["key"] == "palette" for f in s["fields"]}
    assert fields["file"]["options"], "a select the UI cannot use"


@pytest.mark.parametrize("spec", sorted(s["key"] for s in definitive.catalogue()))
def test_every_layer_field_carries_an_explanation(http, spec):
    served = {s["key"]: s for s in http.get("/api/editor/layers")["layers"]}[spec]
    assert served["summary"].strip(), "no summary"
    keys = [f["key"] for f in served["fields"]]
    assert len(keys) == len(set(keys)), "repeats a field key"
    for f in served["fields"]:
        assert f["help"].strip(), f"{spec}.{f['key']} has no help"
        if f["kind"] == "select":
            assert f["options"], f"{spec}.{f['key']} is a select with no options"


@pytest.mark.parametrize("group", sorted({f.group or "-" for f in FIELDS}))
def test_every_config_field_carries_an_explanation(http, group):
    # The layer fields have had this since they were written; the 137 config fields never did, and twenty of them shipped a (?) that opened onto the word TODO.
    served = [f for f in http.get("/api/schema")["fields"]
              if (f.get("group") or "-") == group]
    assert served, f"group '{group}' serves no field"
    for f in served:
        assert f["help"].strip(), f"{f['path']} has no help"
        assert f["help"].strip().rstrip(".").lower() not in Field.PLACEHOLDERS, \
            f"{f['path']} says {f['help']!r}"


def test_a_missing_run_is_a_404_that_names_it(http):
    # — the detail route, which read runs_dir() and raised a bare FileNotFoundError that reached the client as a 500.
    code, body = http.failure("/api/run?id=does_not_exist")
    assert code == 404
    assert body["kind"] == "not_found"
    assert "does_not_exist" in body["error"]


def test_a_bad_config_name_is_a_400_naming_the_field(http):
    code, body = http.failure("/api/config?name=has%20a%20space",
                              {"config": {}}, "PUT")
    assert code == 400
    assert body["kind"] == "invalid"
    assert body["detail"]["field"] == "name"


@pytest.fixture
def config_file(root):
    path = paths.resolve(root, "configs") / "knight_attack.yaml"
    before = path.read_text()
    yield path
    path.write_text(before)


def test_an_out_of_range_save_is_refused(http, config_file):
    code, body = http.failure(
        "/api/config?name=knight_attack",
        {"config": {"canonical": {"steps": 100000}}}, "PUT")
    assert code == 400
    assert body["kind"] == "invalid"
    assert "canonical.steps" in body["error"]


def test_an_in_range_save_still_succeeds(http, config_file):
    code = http.status(
        "/api/config?name=knight_attack",
        {"config": {"canonical": {"steps": 30}}}, "PUT")
    assert code == 200


def test_a_key_the_schema_does_not_declare_passes_through(http, config_file):
    code = http.status(
        "/api/config?name=knight_attack",
        {"config": {"canonical": {"totally_unknown_key": 5}}}, "PUT")
    assert code == 200
