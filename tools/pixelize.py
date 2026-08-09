#!/usr/bin/env python3
"""CLI wrapper. The implementation lives in pipeline/pixelize.py so that
pipeline stages can import it directly instead of shelling out."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.pixelize import main

if __name__ == "__main__":
    raise SystemExit(main())
