from __future__ import annotations

import json

import pytest

from pipeline.geometry import rigs
from pipeline.orchestration import artifacts as io


class Opaque:
    pass


def test_scratch_keys_stay_out_of_the_manifest(tmp_path):
    io.save(tmp_path, {"skeletons": [tmp_path / "a.png"], "_rig": rigs.HUMANOID},
            ["pose"])
    data = json.loads((tmp_path / io.MANIFEST).read_text())
    assert "_rig" not in data["artifacts"], "a Rig was persisted as a repr"
    assert "skeletons" in data["artifacts"], "real artifacts were dropped"


def test_an_unpersistable_artifact_is_refused_not_repr_d(tmp_path):
    with pytest.raises(TypeError, match="resumable"):
        io.save(tmp_path, {"thing": Opaque()}, [])


def test_a_scratch_key_is_the_supported_way_to_keep_one_out(tmp_path):
    io.save(tmp_path, {"_thing": Opaque(), "n": 1}, [])
    loaded, _ = io.load(tmp_path)
    assert loaded == {"n": 1}


class TestPathMap:
    """One image per view, which is what canonical hands back."""

    def test_a_dict_of_paths_round_trips(self):
        from pathlib import Path

        from pipeline.orchestration.artifacts import _decode, _encode

        made = {"front": Path("a.png"), "side": Path("b.png")}
        assert _decode(_encode(made)) == made

    def test_it_survives_the_manifest(self, tmp_path):
        """canonical crashed the run at the end, after the images were written."""
        from pathlib import Path

        from pipeline.orchestration import artifacts

        made = {"front": tmp_path / "a.png", "side": tmp_path / "b.png"}
        for one in made.values():
            one.write_bytes(b"")
        artifacts.save(tmp_path, {"canonical": made["front"], "canonicals": made},
                       ["canonical"])
        back, completed = artifacts.load(tmp_path)
        assert back["canonicals"] == made
        assert completed == ["canonical"]

    def test_a_dict_of_something_else_is_still_refused(self):
        from pipeline.orchestration.artifacts import _encode

        class Opaque:
            pass

        with pytest.raises(TypeError) as caught:
            _encode({"front": Opaque()})
        # The message has to name what was in it, or the next person reads
        # "dict cannot be persisted" and has no idea which key or what type.
        assert "Opaque" in str(caught.value)

    def test_an_empty_dict_is_plain_json(self):
        from pipeline.orchestration.artifacts import _decode, _encode

        assert _decode(_encode({})) == {}


def test_a_queued_job_waits_for_the_machine_rather_than_failing(monkeypatch):
    """Blocked because the GPU is busy is not a broken job."""
    from pathlib import Path

    from pipeline.orchestration import queue as q
    from pipeline.shared import guard

    monkeypatch.setattr(guard, "run_in_flight", lambda: "20260101_000000_x")
    waiting = q._await_free_gpu(Path("."))
    assert waiting and "20260101_000000_x" in waiting[0]

    monkeypatch.setattr(guard, "run_in_flight", lambda: None)
    assert q._await_free_gpu(Path(".")) == []
