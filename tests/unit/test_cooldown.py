"""The cooldown between two generations, and the floor under it."""

from __future__ import annotations

import time

from pipeline.orchestration import queue as q


def test_a_cooldown_shorter_than_the_floor_is_raised_to_it():
    """The window exists to hand the weights back; below the floor it does not."""
    for asked in (None, 0, -5, 3, 9.9, "", "nonsense"):
        assert q.cooldown_seconds(asked) == q.COOLDOWN_FLOOR_S


def test_a_longer_cooldown_is_taken_as_asked():
    assert q.cooldown_seconds(45) == 45.0
    assert q.cooldown_seconds("30") == 30.0


def test_the_wait_is_spent_freeing_and_then_resting(monkeypatch, tmp_path):
    """Freeing first is the point: a bare sleep would rest and hand nothing back."""
    order = []

    class Fake:
        host = "http://x"

        def free_models(self):
            order.append("freed")
            time.sleep(0.05)
            return True

    monkeypatch.setattr(q, "comfy_client", lambda root: Fake())
    slept = []
    monkeypatch.setattr(q.time, "sleep", lambda s: slept.append(s))

    assert q.cooldown(tmp_path, 0.2) is True
    assert order == ["freed"]
    assert slept and slept[0] < 0.2, "rested the whole window on top of the free"


def test_a_refused_free_is_reported_not_swallowed(monkeypatch, tmp_path):
    class Dead:
        host = "http://x"

        def free_models(self):
            return False

    monkeypatch.setattr(q, "comfy_client", lambda root: Dead())
    monkeypatch.setattr(q.time, "sleep", lambda s: None)
    assert q.cooldown(tmp_path, 10) is False
