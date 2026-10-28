#!/usr/bin/env python3
"""Command-line entry point; all analysis implementation lives in src."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cad_inventory.pipeline import main

if __name__ == '__main__':
    raise SystemExit(main())
