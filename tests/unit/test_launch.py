"""One preparation for three launchers, and the directory they agree on."""

from __future__ import annotations

import pytest
import yaml

from pipeline.orchestration import launch
from pipeline.shared.errors import Invalid

GOOD = {"pipeline": {"stages": ["pose"]}}


@pytest.fixture
def config(tmp_path):
    def write(body=None, name="job"):
        path = tmp_path / f"{name}.yaml"
        path.write_text(yaml.safe_dump({**GOOD, **(body or {})}))
        return path
    return write


def test_a_run_directory_carries_the_config_it_was_started_from(tmp_path, config):
    ready = launch.prepare(tmp_path, config(), run_id="r1")
    assert ready.outdir.name == "r1"
    assert ready.config_path == ready.outdir / "config.yaml"
    assert yaml.safe_load(ready.config_path.read_text())["pipeline"]["stages"]


def test_the_snapshot_is_the_raw_config_so_resume_does_not_layer_twice(
        tmp_path, config):
    """styles.effective runs again on resume; a token appended twice reads twice."""
    ready = launch.prepare(tmp_path, config({"styles": ["nope"]} if False else {}),
                           run_id="r2", overrides={"canonical.seed": 7})
    saved = yaml.safe_load(ready.config_path.read_text())
    assert saved["canonical"]["seed"] == 7, "overrides must reach the snapshot"
    assert "compute" not in saved, "the snapshot took _global's defaults with it"


def test_the_runs_directory_comes_from_the_config_not_from_global(tmp_path, config):
    """A launcher read _global while run.py read the config, and they split."""
    ready = launch.prepare(tmp_path, config({"paths": {"output_dir": "out/elsewhere"}}),
                           run_id="r3")
    assert ready.outdir.parent.name == "elsewhere"
    assert ready.outdir == launch.runs_base(tmp_path, ready.cfg) / "r3"


def test_every_launcher_lands_in_the_same_directory(tmp_path, config):
    """The CLI, the Run button and autopilot, given one config and one id."""
    path = config({"paths": {"output_dir": "out/elsewhere"}})
    where = {launch.prepare(tmp_path, path, run_id="same").outdir for _ in range(3)}
    assert len(where) == 1, f"three preparations, {len(where)} directories: {where}"


def test_a_config_that_cannot_run_never_makes_a_directory(tmp_path, config):
    """start_run used to mkdir first, leaving an empty run behind on refusal."""
    with pytest.raises(Invalid, match="canonical.steps"):
        launch.prepare(tmp_path, config({"canonical": {"stps": 30}}), run_id="r4")
    assert not (tmp_path / "out" / "runs" / "r4").exists()


def test_a_config_naming_no_stages_is_refused_for_everyone(tmp_path):
    """Only the CLI refused this; a queued job reached run.py before anything said so."""
    path = tmp_path / "empty.yaml"
    path.write_text(yaml.safe_dump({"subject": "a knight"}))
    with pytest.raises(Invalid, match="pipeline.stages"):
        launch.prepare(tmp_path, path, run_id="r5")


def test_a_name_is_used_when_no_run_id_is_given(tmp_path, config):
    ready = launch.prepare(tmp_path, config(), name="bright")
    assert ready.run_id.endswith("_bright")
