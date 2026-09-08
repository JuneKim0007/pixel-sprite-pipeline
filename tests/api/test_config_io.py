from __future__ import annotations

import pytest

from pipeline.shared import paths

NAME = "knight_attack"


@pytest.fixture
def config_file(root):
    path = paths.resolve(root, "configs") / f"{NAME}.yaml"
    before = path.read_text()
    yield path
    path.write_text(before)


def test_a_save_preserves_comments_and_applies_the_value(http, config_file):
    comments = config_file.read_text().count("#")
    http.send(f"/api/config?name={NAME}", {"config": {"canonical": {"seed": 4242}}},
              "PUT")
    after = config_file.read_text()
    assert after.count("#") == comments, f"{comments} -> {after.count('#')} comments"
    assert "seed: 4242" in after, "value not written"


def test_an_unrunnable_stage_order_is_rejected_without_damaging_the_file(
        http, config_file):
    before = config_file.read_text()
    code = http.status(f"/api/config?name={NAME}",
                       {"config": {"pipeline": {"stages": ["frames", "pose"]}}},
                       "PUT")
    assert code == 400, "an unrunnable order was accepted"
    assert config_file.read_text() == before, "a rejected save damaged the file"


def test_the_index_carries_each_config_module(http):
    """The rail filters by module; learning it cost one full fetch per config."""
    configs = http.get("/api/configs")["configs"]
    assert configs, "no configs listed"
    assert all(isinstance(c, dict) for c in configs), "index is still bare names"
    assert {"name", "module", "error"} <= set(configs[0])

    by_name = {c["name"]: c for c in configs}
    assert by_name["knight_attack"]["module"] == "animation"
    assert by_name["character_sheet"]["module"] == "character_sheet"
    assert not any(c["error"] for c in configs), "a config failed to parse"


def test_a_config_that_will_not_parse_is_listed_rather_than_hidden(http, root):
    """Dropping it from the picker leaves nobody able to find it to repair it."""
    broken = paths.resolve(root, "configs") / "not_yaml.yaml"
    broken.write_text("stages: [a, b\n  bad: : :\n")
    try:
        found = {c["name"]: c for c in http.get("/api/configs")["configs"]}
        assert "not_yaml" in found, "an unparseable config vanished from the index"
        assert found["not_yaml"]["error"], "no reason given for the broken config"
    finally:
        broken.unlink()


def test_a_new_name_creates_the_file(http, root):
    """The UI had no way to make a pipeline; the route always could."""
    made = paths.resolve(root, "configs") / "made_by_test.yaml"
    try:
        http.send("/api/config?name=made_by_test",
                  {"config": {"name": "made_by_test", "module": "animation",
                              "pipeline": {"stages": ["pose", "depth", "canonical",
                                                      "frames", "palette", "export"]}}},
                  "PUT")
        assert made.is_file(), "PUT with a fresh name wrote nothing"
        found = {c["name"]: c for c in http.get("/api/configs")["configs"]}
        assert found["made_by_test"]["module"] == "animation"
    finally:
        made.unlink(missing_ok=True)
