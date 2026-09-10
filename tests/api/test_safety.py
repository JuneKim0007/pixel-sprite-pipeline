from __future__ import annotations

import urllib.parse

import pytest


@pytest.mark.parametrize("target", ["../../etc/passwd", "/etc/passwd"])
def test_path_traversal_is_blocked(http, target):
    # Absolute fails containment (403); relative dies as a missing file (404).
    code = http.status(f"/api/file?path={urllib.parse.quote(target)}")
    assert code in (403, 404), f"answered {code}"
