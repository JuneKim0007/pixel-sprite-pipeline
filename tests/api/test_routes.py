from __future__ import annotations

import pytest

from pipeline import api, definitive
from pipeline.generation.schema import FIELDS
from pipeline.shared import paths
from pipeline.shared.contracts import Field

# What a GET route needs before it can answer at all. A route missing an entry
# is called bare, which is a 400 and proves nothing about its shape — so this
# table is what decides whether the contract test actually exercises a route.
ARGS = {
    "/api/config": "?name=char_1",
    "/api/rigpose": "?rig=humanoid",
    "/api/style/detail": "?name=retro_jrpg",
    "/api/style/preview": "?config=character_sheet",
    "/api/style/training": "?name=retro_jrpg",
    "/api/annotation": "?image=README.md",
    "/api/file": "?path=README.md",
}
NEEDS_ARG = {"/api/autorig", "/api/run", "/api/run/poses"}
ROUTES = {r["path"]: r for r in api.table.surface() if r["method"] == "GET"}
GETS = sorted(ROUTES)


@pytest.mark.parametrize("path", GETS)
def test_every_declared_get_route_answers(http, path):
    code = http.status(path + ARGS.get(path, ""))
    assert code < 500, f"{path} fails server-side with {code}"
    if path not in NEEDS_ARG:
        assert code == 200, f"{path} answered {code}"


@pytest.mark.parametrize("path", sorted(p for p in GETS if p not in NEEDS_ARG))
def test_a_response_matches_the_contract_its_route_declares(http, path):
    # /api/config returned four of seven keys once and every route still answered 200; the only symptom was the whole UI failing to start. GET /api/annotation was worse — it called annotate.load with three arguments where it takes one, so every real call was a 500, and nothing noticed because the bare call 400s before it gets there.
    contract = ROUTES[path]["returns"]
    body = http.raw(path + ARGS.get(path, ""))
    faults = contract.check(body)
    assert not faults, f"{path} declares {contract} but " + "; ".join(faults)


def test_a_side_effect_free_post_honours_its_contract_too(http):
    # The `http` fixture checks every call against its route's contract, so a POST is covered by whatever already exercises it. Only three POSTs can be called without leaving something behind; the rest are declared and checked at import, not against a live body.
    http.send("/api/queue/autopilot", {"action": "stop"})


@pytest.fixture
def a_run():
    """A minimal run on disk. It has to live under the real runs_dir(): the server under test runs in this process's ROOT, so a tmp_path is invisible to it."""
    import json
    import shutil

    from pipeline.api.context import runs_dir

    home = runs_dir() / "20260101_000000_test"
    pose = home / "03_pose"
    pose.mkdir(parents=True)
    (home / "run.log").write_text("ok\n")
    (pose / "pose.json").write_text(json.dumps(
        {"source": "library", "rig": "humanoid", "mode": "set",
         "entries": [{"pose": {}, "yaw": 0, "spec": 0}]}))
    try:
        yield home.name
    finally:
        shutil.rmtree(home, ignore_errors=True)


@pytest.mark.parametrize("path,query", [
    ("/api/run", "?id={run}"),
    ("/api/run/poses", "?run={run}"),
])
def test_a_run_scoped_route_honours_its_contract(http, a_run, path, query):
    # These three could only be called bare, which 400s in the argument check and proves nothing about the body — the same blind spot that hid a TypeError in /api/annotation for as long as that route existed.
    body = http.raw(path + query.format(run=a_run))
    faults = ROUTES[path]["returns"].check(body)
    assert not faults, "; ".join(faults)


def test_every_route_declares_what_it_returns():
    missing = [f'{r["method"]} {r["path"]}' for r in api.table.surface()
               if r["returns"] is None]
    assert not missing, missing


def test_a_route_without_a_contract_cannot_be_declared():
    from pipeline.api.routing import get as declare
    from pipeline.shared.errors import Invalid

    with pytest.raises(Invalid, match="no response contract"):
        declare("/nope", "a route nobody promised anything about")


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
