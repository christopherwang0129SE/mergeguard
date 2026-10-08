"""Shared fixtures. Model calls stay off unless a test opts in."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _isolate_nebius_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore a developer .env so tests never hit Token Factory by accident."""
    monkeypatch.setattr(settings, "nebius_api_key", "")
    monkeypatch.setattr(settings, "nebius_enabled", True)
    monkeypatch.setattr(settings, "nebius_max_pairs", 6)
    monkeypatch.setattr(settings, "nebius_timeout_seconds", 40.0)
    monkeypatch.setattr(settings, "nebius_model", "nvidia/nemotron-3-super-120b-a12b")
    monkeypatch.setattr(
        settings,
        "nebius_base_url",
        "https://api.tokenfactory.us-central1.nebius.com/v1/",
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _write(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "demo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")

    base_lines = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    _write(r, "app.py", base_lines)
    _write(r, "readme.md", "# demo\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")

    # feat-a: edit lines ~2-4 of app.py
    _git(r, "checkout", "-q", "-b", "feat-a")
    lines = base_lines.splitlines()
    lines[2] = "line 3 CHANGED BY A"
    _write(r, "app.py", "\n".join(lines) + "\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "a edits top")

    # feat-b: edit line ~3 too (overlaps feat-a) + an unrelated file
    _git(r, "checkout", "-q", "main")
    _git(r, "checkout", "-q", "-b", "feat-b")
    lines = base_lines.splitlines()
    lines[2] = "line 3 CHANGED BY B"
    _write(r, "app.py", "\n".join(lines) + "\n")
    _write(r, "unrelated.py", "print('b only')\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "b edits top + new file")

    # feat-c: edits far-away lines of app.py (shared file, no overlap)
    _git(r, "checkout", "-q", "main")
    _git(r, "checkout", "-q", "-b", "feat-c")
    lines = base_lines.splitlines()
    lines[18] = "line 19 CHANGED BY C"
    _write(r, "app.py", "\n".join(lines) + "\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "c edits bottom")

    _git(r, "checkout", "-q", "main")
    return r
