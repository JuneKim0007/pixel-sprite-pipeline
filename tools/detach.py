#!/usr/bin/env python3
"""Run a command in its own session, so no group kill upstream can reach it.
macOS ships no setsid(1), and an inherited process group took the sweep down ten times."""

import os
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if os.fork() > 0:
        return 0
    os.setsid()
    try:
        os.execvp(sys.argv[1], sys.argv[1:])
    except OSError as err:
        print(f"detach: {sys.argv[1]}: {err}", file=sys.stderr)
        os._exit(127)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
