"""Reading configuration, without knowing what any of it means.

`opt` lived in stage.py, next to the Stage contract and a 128-line service
locator, so a module that wanted to read one config key imported all three.
props.py is the clearest case: it describes where a sword points and imported
the stage contract for a ten-line helper.

Nothing here knows about stages, rigs or references. That is the rule for
everything under shared/ - membership is decided by having no dependency on a
module, not by being generally useful.
"""

from __future__ import annotations

from typing import Any


def opt(cfg: dict, key: str, default: Any) -> Any:
    """cfg.get with blank YAML keys treated as absent.

    `size:` with nothing after it parses to None, not to a missing key, so
    dict.get(key, default) hands back None and the default never applies. The
    commented configs are full of intentionally-blank keys meaning "use the
    default", so every read of them has to go through here.
    """
    value = cfg.get(key, None)
    return default if value is None else value
