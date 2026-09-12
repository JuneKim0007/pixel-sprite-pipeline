from __future__ import annotations

import pytest

from pipeline import api, definitive
from pipeline.generation.schema import FIELDS
from pipeline.shared import paths
from pipeline.shared.contracts import Field

# What a GET route needs before it can answer at all.
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
    # /api/config once returned four of seven keys and every route still answered 200.
    contract = ROUTES[path]["returns"]
    body = http.raw(path + ARGS.get(path, ""))
    faults = contract.check(body)
    assert not faults, f"{path} declares {contract} but " + "; ".join(faults)


def test_a_side_effect_free_post_honours_its_contract_too(http):
    # The `http` fixture checks every call against its route's contract.
    http.send("/api/queue/autopilot", {"action": "stop"})


@pytest.fixture
def a_run():
    """A minimal run under the real runs_dir(): a tmp_path is invisible to the server."""
    import json
    import shutil

    from PIL import Image

    from pipeline.api.context import runs_dir

    home = runs_dir() / "20260101_000000_test"
    pose = home / "03_pose"
    pose.mkdir(parents=True)
    (home / "run.log").write_text("ok\n")
    (home / "config.yaml").write_text("name: test\nrig: humanoid\nannotate: skip\n")
    Image.new("RGB", (32, 32), (120, 60, 30)).save(pose / "skeleton_000.png")
    (pose / "pose.json").write_text(json.dumps(
        {"source": "library", "rig": "humanoid", "mode": "set",
         "entries": [{"pose": {}, "yaw": 0, "spec": 0}]}))
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _writes(home):
    """The write routes that can be called without leaving anything behind."""
    image = str(home / "03_pose" / "skeleton_000.png")
    return [
        ("/api/download/plan", {"run_id": home.name}),
        ("/api/edit/preview", {"source": image, "full": False}),
        ("/api/edit/apply", {"source": image, "dest": str(home / "px.png")}),
        ("/api/annotation", {"image": image, "rig": "humanoid",
                             "points": {"nose": [1.0, 2.0]}}),
        ("/api/poses", {"run_id": home.name, "entries": []}),
    ]


@pytest.mark.parametrize("index", range(5))
def test_a_write_route_honours_its_contract(http, a_run, index):
    # Declaring a contract at import proves only that one exists.
    path, payload = _writes(a_run)[index]
    http.send(path, payload)          # the fixture asserts the contract


@pytest.mark.parametrize("path,query", [
    ("/api/run", "?id={run}"),
    ("/api/run/poses", "?run={run}"),
])
def test_a_run_scoped_route_honours_its_contract(http, a_run, path, query):
    # Called bare, they 400 in the argument check and prove nothing about the body.
    body = http.raw(path + query.format(run=a_run.name))
    faults = ROUTES[path]["returns"].check(body)
    assert not faults, "; ".join(faults)


def _global_leaves():
    from pipeline.shared.settings import DEFAULT_GLOBAL

    def leaves(node, prefix=""):
        for key, value in node.items():
            here = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                yield from leaves(value, here)
            else:
                yield here, value
    return {k: v for k, v in leaves(DEFAULT_GLOBAL) if v is not None}


def test_a_machine_default_reaches_the_form(http):
    from pipeline.generation.schema import SCHEMA

    served = {f["path"]: f for f in http.get("/api/schema")["fields"]}
    silent = [path for path in _global_leaves()
              if SCHEMA.field(path) is not None and path in served
              and "default" not in served[path]]
    assert not silent, (
        f"{silent} are supplied to every run and the form offers no default")


def test_a_setting_is_declared_in_one_place_or_the_other():
    from pipeline.generation.schema import SCHEMA

    both = [path for path, value in _global_leaves().items()
            if (f := SCHEMA.field(path)) is not None and f.default is not None
            and f.default == value]
    assert not both, (
        f"{both} are declared on the field AND in DEFAULT_GLOBAL. A machine "
        f"fact belongs in shared/settings.py; a universal one belongs on the "
        f"field. Never both.")


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
    # Twenty config fields shipped a (?) that opened onto the word TODO.
    served = [f for f in http.get("/api/schema")["fields"]
              if (f.get("group") or "-") == group]
    assert served, f"group '{group}' serves no field"
    for f in served:
        assert f["help"].strip(), f"{f['path']} has no help"
        assert f["help"].strip().rstrip(".").lower() not in Field.PLACEHOLDERS, \
            f"{f['path']} says {f['help']!r}"


def test_a_missing_run_is_a_404_that_names_it(http):
    # The detail route once raised a bare FileNotFoundError: a 500 to the client.
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


def test_a_key_the_schema_does_not_declare_is_refused(http, config_file):
    """900833f let these through because the schema was not exhaustive."""
    code = http.status(
        "/api/config?name=knight_attack",
        {"config": {"canonical": {"totally_unknown_key": 5}}}, "PUT")
    assert code == 400


def test_every_asset_type_says_what_it_makes_and_what_it_costs(http):
    """The kinds banner reads this: a picture in, a picture out, and the
    dials that change the result, each with words a non-technical reader
    can act on."""
    body = http.get("/api/modules")
    assert body["modules"], "no asset types served"
    assert body["dials"], "no plain-language descriptions served"

    for key, spec in body["modules"].items():
        if spec.get("error"):
            continue
        assert spec["label"] and spec["blurb"], key
        assert isinstance(spec["shots"], dict), key
        assert isinstance(spec["values"], dict), key
        if not spec["available"]:
            assert spec["missing"], f"{key} is unavailable but names no stage"

    for path, d in body["dials"].items():
        for part in ("label", "plain", "more", "less"):
            assert d[part].strip(), f"{path}.{part} is empty"
