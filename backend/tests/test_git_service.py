"""git_service.textual_conflict against a real temporary repo."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.services.git_service import textual_conflict


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def test_textual_conflict_on_a_real_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("one\ntwo\nthree\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    _git(repo, "checkout", "-q", "-b", "left")
    (repo / "app.py").write_text("one\nLEFT\nthree\n")
    _git(repo, "commit", "-qam", "left")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "right")
    (repo / "app.py").write_text("one\nRIGHT\nthree\n")
    _git(repo, "commit", "-qam", "right")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "other.py").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "other")
    _git(repo, "checkout", "-q", "main")

    assert textual_conflict(repo, "left", "right") is True
    assert textual_conflict(repo, "left", "other") is False
    assert textual_conflict(repo, "left", "no-such-branch") is None
    assert textual_conflict(tmp_path / "missing", "left", "right") is None
