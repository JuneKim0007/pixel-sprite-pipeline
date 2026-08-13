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
