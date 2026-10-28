#!/usr/bin/env python3
"""Run local manufacturing quality, CAD joins, and dependency analysis."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from cad_inventory.manufacturing.pipeline import main

if __name__=='__main__':
    raise SystemExit(main())
