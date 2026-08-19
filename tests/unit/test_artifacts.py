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
