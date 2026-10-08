#!/usr/bin/env python3
"""Replay case 1 (pymc-labs/daimon PR #14 x PR #17): kept for old links.

Same as ``python3 demo/replay.py --case real-case-1 [--prove|--fresh|--no-venv|--dest]``;
the pinned SHAs and the story live in demo/replay.py and docs/HALL_OF_FAME.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replay  # noqa: E402

if __name__ == "__main__":
    sys.argv[1:1] = ["--case", "real-case-1"]
    raise SystemExit(replay.main())
