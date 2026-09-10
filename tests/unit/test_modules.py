from __future__ import annotations

import pytest
import yaml

from pipeline.shared import modules
from pipeline.shared.errors import Invalid, NotFound


@pytest.fixture
def types(tmp_path):
    """A root of its own. The `root` fixture is the real repository, and a test
    that writes an asset type into it leaves the file there."""
    modules.all(tmp_path)          # seeds the builtins
    return tmp_path


def write(root, key, **body):
    body.setdefault("label", key.title())
    body.setdefault("detail", "a detail")
    body.setdefault("blurb", "a blurb")
    (modules.directory(root) / f"{key}.yaml").write_text(
        yaml.safe_dump(body, sort_keys=False))


def test_the_builtins_are_seeded_as_files(types):
    """A dict in schema.py could not be edited; a file can."""
    names = {p.stem for p in modules.directory(types).glob("*.yaml")}
    assert {"animation", "character_sheet", "tileset", "object"} <= names


def test_the_props_flag_reproduces_the_string_compare_it_replaced(types):
    """props.py asked `module != "character_sheet"`. Same answers, as data."""
    assert modules.wants_props(types, "character_sheet") is False
    assert modules.wants_props(types, "animation") is True
    assert modules.wants_props(types, None) is True
    assert modules.wants_props(types, "no_such_type") is True


def test_an_unknown_key_is_refused_by_name(types):
    """A typo in a hand-written type is a message, not a silently ignored line."""
    write(types, "typo", stagez=["pose"])
    with pytest.raises(Invalid) as e:
        modules.get(types, "typo")
    assert "stagez" in str(e.value)


def test_a_malformed_file_is_broken_rather_than_absent(types):
    """Omitting it leaves someone wondering where their type went."""
    (modules.directory(types) / "bad.yaml").write_text("label: [unclosed\n")
    assert any(b.path.stem == "bad" for b in modules.registry(types).broken())


def test_a_missing_type_says_what_does_exist(types):
    with pytest.raises(NotFound):
        modules.get(types, "nothing_like_this")


def test_lineage_walks_the_extends_chain(types):
    write(types, "portrait", extends="character_sheet", stages=["pose"])
    write(types, "bust", extends="portrait", stages=["pose"])
    assert modules.lineage(types, "bust") == ["bust", "portrait", "character_sheet"]
    assert modules.lineage(types, "animation") == ["animation"]


def test_a_cycle_in_extends_is_refused_rather_than_hung(types):
    write(types, "ping", extends="pong", stages=["pose"])
    write(types, "pong", extends="ping", stages=["pose"])
    with pytest.raises(Invalid):
        modules.lineage(types, "ping")


def test_a_type_may_declare_a_stage_that_does_not_exist(types):
    """That is how tileset states the work it is waiting on."""
    write(types, "weather", stages=["cloud_field", "canonical"])
    assert modules.get(types, "weather").stages == ["cloud_field", "canonical"]


class TestAssetTypeDefaults:
    """What a type starts from, when a config does not say."""

    def test_a_sheet_starts_from_the_rig_not_an_animation_frame(self, tmp_path):
        from pipeline.shared import modules

        assert modules.defaults_for(tmp_path, "character_sheet") == {
            "pose.source": "tpose"}

    def test_a_type_that_declares_none_gets_none(self, tmp_path):
        from pipeline.shared import modules

        assert modules.defaults_for(tmp_path, "animation") == {}

    def test_an_unknown_type_is_not_an_error(self, tmp_path):
        from pipeline.shared import modules

        assert modules.defaults_for(tmp_path, "no_such_type") == {}

    def test_a_config_that_names_its_own_wins(self, tmp_path):
        """The type says what unset means; it never overrides what was said."""
        from pipeline.generation.stage import Context

        ctx = Context(root=tmp_path, config={"module": "character_sheet",
                                             "pose": {"source": "library"}},
                      run_id="r", outdir=tmp_path)
        assert ctx.settings("pose.source") == "library"

    def test_an_unset_field_takes_the_type_s_default(self, tmp_path):
        from pipeline.generation.stage import Context

        ctx = Context(root=tmp_path, config={"module": "character_sheet"},
                      run_id="r", outdir=tmp_path)
        assert ctx.settings("pose.source") == "tpose"

    def test_another_type_still_takes_the_field_default(self, tmp_path):
        from pipeline.generation.stage import Context

        ctx = Context(root=tmp_path, config={"module": "animation"},
                      run_id="r", outdir=tmp_path)
        assert ctx.settings("pose.source") == "library"
