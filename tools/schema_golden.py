"""The settings form as one line per field, so two branches can both edit it.

    python tools/schema_golden.py            print
    python tools/schema_golden.py --write    rewrite the golden

Expanded JSON put seven lines on screen for every field and repeated all 162 of
them across seven asset types: 8092 lines, regenerated whole by any branch that
declares a field. Two branches doing that at once is 686 lines for git to
reconcile over a file nobody reads. One sorted line per field means two
branches adding different fields touch different lines.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GOLDEN = ROOT / "tests/golden/schema_fields.txt"


def lines() -> list[str]:
    from pipeline.generation import schema
    from pipeline.shared import modules

    out = []
    for module in [None, *modules.BUILTIN]:
        name = "null" if module is None else module
        for field in schema.fields_for(module):
            body = json.dumps(field, sort_keys=True, separators=(",", ":"),
                              default=str)
            out.append(f"{name}\t{field['path']}\t{body}")
    return sorted(out)


def main() -> int:
    text = "\n".join(lines()) + "\n"
    if "--write" in sys.argv:
        GOLDEN.write_text(text)
        print(f"{GOLDEN.relative_to(ROOT)}: {len(lines())} fields")
        return 0
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
