#!/usr/bin/env python3
"""Build demo/harmless-demo: branch pairs that look related but do NOT conflict.

Used to check that MergeGuard never reports "confirmed by test" on a harmless
pair. Every branch passes its tests alone, every pair merges cleanly, and every
merge passes all tests.

    main                 geometry.py (area, perimeter) + report.py (describe)
    feat/area-scale      area(w, h, scale=1): new OPTIONAL parameter
    feat/summary         report.summary() calls area(w, h) and perimeter(w, h)
    refactor/perimeter   perimeter() rewritten, same results
    docs/changelog       CHANGELOG.md only

Pairs worth noting: area-scale x summary (signature change, every call still
matches), summary x refactor/perimeter (body change under a new caller, same
behaviour), area-scale x refactor/perimeter (same file, different functions).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DEST = HERE / "harmless-demo"

GEOMETRY = '''"""Rectangle helpers."""


def area(w, h):
    return w * h


# Perimeter helpers below. Kept apart from area() on purpose: these blank
# lines and comments keep the two functions' hunks well apart.
#
#


def perimeter(w, h):
    return 2 * (w + h)
'''

REPORT = '''from geometry import area


def describe(w, h):
    return f"{w}x{h} rectangle, area {area(w, h)}"
'''

TESTS = '''from geometry import area, perimeter
from report import describe


def test_area():
    assert area(2, 3) == 6


def test_perimeter():
    assert perimeter(2, 3) == 10


def test_describe():
    assert describe(2, 3) == "2x3 rectangle, area 6"
'''


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def build(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    git(dest, "init", "-q", "-b", "main")
    git(dest, "config", "user.email", "demo@mergeguard.local")
    git(dest, "config", "user.name", "MergeGuard Demo")
    (dest / "geometry.py").write_text(GEOMETRY)
    (dest / "report.py").write_text(REPORT)
    (dest / "test_geometry.py").write_text(TESTS)
    (dest / "README.md").write_text("# Geometry helpers\n")
    commit_all(dest, "geometry helpers with tests")

    git(dest, "checkout", "-q", "-b", "feat/area-scale")
    (dest / "geometry.py").write_text(GEOMETRY.replace(
        "def area(w, h):\n    return w * h",
        'def area(w, h, scale=1):\n    """Area, optionally scaled (default: unscaled)."""\n'
        "    return w * h * scale",
    ))
    (dest / "test_geometry.py").write_text(
        TESTS + "\n\ndef test_area_scaled():\n    assert area(2, 3, scale=2) == 12\n"
    )
    commit_all(dest, "area: optional scale factor (default 1, unchanged results)")

    git(dest, "checkout", "-q", "main")
    git(dest, "checkout", "-q", "-b", "feat/summary")
    (dest / "report.py").write_text(
        REPORT.replace("from geometry import area", "from geometry import area, perimeter")
        + '\n\ndef summary(w, h):\n    return {"area": area(w, h), "perimeter": perimeter(w, h)}\n'
    )
    (dest / "test_report.py").write_text(
        "from report import summary\n\n\n"
        'def test_summary():\n    assert summary(2, 3) == {"area": 6, "perimeter": 10}\n'
    )
    commit_all(dest, "report: summary() with area and perimeter")

    git(dest, "checkout", "-q", "main")
    git(dest, "checkout", "-q", "-b", "refactor/perimeter")
    (dest / "geometry.py").write_text(GEOMETRY.replace(
        "    return 2 * (w + h)", "    half = w + h\n    return half + half"
    ))
    commit_all(dest, "refactor perimeter (same results)")

    git(dest, "checkout", "-q", "main")
    git(dest, "checkout", "-q", "-b", "docs/changelog")
    (dest / "CHANGELOG.md").write_text("# Changelog\n\n- Geometry helpers.\n")
    commit_all(dest, "docs: changelog")
    git(dest, "checkout", "-q", "main")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args()
    build(args.dest.resolve())
    print(f"harmless demo repo: {args.dest.resolve()}")


if __name__ == "__main__":
    main()
