"""POST /api/pair-diff and the git helper behind it."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import git_service, sentinel, verifier


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init(path: Path) -> None:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")


def _kinds(side: dict) -> list[str]:
    return [line["kind"] for hunk in side["hunks"] for line in hunk["lines"]]


def test_pair_diff_shared_file_overlap_and_line_numbers(repo: Path) -> None:
    response = TestClient(app).post(
        "/api/pair-diff",
        json={
            "repo_path": str(repo),
            "base_branch": "main",
            "branch_a": "feat-a",
            "branch_b": "feat-b",
            "context": 1,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["base_branch"] == "main"
    assert body["branch_a"] == "feat-a"
    assert body["branch_b"] == "feat-b"
    assert len(body["merge_base_a"]) == 40
    assert body["merge_base_a"] == body["merge_base_b"]

    files = body["files"]
    assert [item["path"] for item in files] == ["app.py", "unrelated.py"]
    shared = files[0]
    assert shared["shared"] is True
    assert shared["overlapping"] is True
    assert shared["a"]["status"] == "modified"
    assert shared["b"]["status"] == "modified"
    assert shared["a"]["truncated"] is False
    assert "CHANGED BY A" in shared["a"]["patch"]
    hunk = shared["a"]["hunks"][0]
    assert hunk["old_start"] >= 1
    changed = [line for line in hunk["lines"] if line["kind"] == "add"]
    assert changed[0]["text"] == "line 3 CHANGED BY A"
    assert changed[0]["old_no"] is None
    assert changed[0]["new_no"] == 3
    removed = [line for line in hunk["lines"] if line["kind"] == "del"]
    assert removed[0]["old_no"] == 3
    assert removed[0]["new_no"] is None
    assert "context" in _kinds(shared["a"])

    only_b = files[1]
    assert only_b["shared"] is False
    assert only_b["overlapping"] is False
    assert only_b["a"]["status"] == "unchanged"
    assert only_b["a"]["patch"] == ""
    assert only_b["a"]["hunks"] == []
    assert only_b["b"]["status"] == "added"
    assert _kinds(only_b["b"]) == ["add"]

    far = TestClient(app).post(
        "/api/pair-diff",
        json={
            "repo_path": str(repo),
            "branch_a": "feat-a",
            "branch_b": "feat-c",
            "context": 3,
        },
    )
    assert far.status_code == 200
    app_py = far.json()["files"][0]
    assert app_py["path"] == "app.py"
    assert app_py["shared"] is True
    assert app_py["overlapping"] is False

    analysis = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"])
    by_pair = {frozenset((p.branch_a, p.branch_b)): p for p in analysis.pairs}
    assert by_pair[frozenset(("feat-a", "feat-b"))].overlapping_files == 1
    assert by_pair[frozenset(("feat-a", "feat-c"))].overlapping_files == 0


def test_context_zero_drops_context_lines(repo: Path) -> None:
    response = TestClient(app).post(
        "/api/pair-diff",
        json={
            "repo_path": str(repo),
            "branch_a": "feat-a",
            "branch_b": "feat-b",
            "context": 0,
        },
    )
    assert response.status_code == 200, response.text
    side = response.json()["files"][0]["a"]
    assert _kinds(side) == ["del", "add"]
    assert "@@ " in side["patch"]


def test_deleted_binary_and_truncated_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    (repo / "keep.py").write_text("print(1)\n")
    (repo / "gone.py").write_text("print('bye')\n")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    _git(repo, "checkout", "-q", "-b", "feat-a")
    (repo / "keep.py").write_text("print(2)\n")
    (repo / "gone.py").unlink()
    (repo / "blob.bin").write_bytes(b"\x00\x01\x03")
    (repo / "aaa_only.txt").write_text("only a\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "feat-b")
    (repo / "keep.py").write_text("print(3)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "b")
    _git(repo, "checkout", "-q", "main")

    monkeypatch.setattr(git_service, "PATCH_MAX_CHARS", 40)
    response = TestClient(app).post(
        "/api/pair-diff",
        json={"repo_path": str(repo), "branch_a": "feat-a", "branch_b": "feat-b", "context": 1},
    )
    assert response.status_code == 200, response.text
    files = {item["path"]: item for item in response.json()["files"]}
    assert response.json()["files"][0]["path"] == "keep.py"
    assert files["keep.py"]["shared"] is True
    assert files["keep.py"]["overlapping"] is True
    assert files["aaa_only.txt"]["shared"] is False
    assert files["aaa_only.txt"]["b"]["status"] == "unchanged"
    assert files["gone.py"]["a"]["status"] == "deleted"
    assert any(line["kind"] == "del" for line in files["gone.py"]["a"]["hunks"][0]["lines"])
    assert files["blob.bin"]["a"]["status"] == "modified"
    assert files["blob.bin"]["a"]["patch"] == ""
    assert files["blob.bin"]["a"]["hunks"] == []
    assert files["blob.bin"]["b"]["status"] == "unchanged"

    # The cap is smaller than a normal unified diff of keep.py.
    assert files["keep.py"]["a"]["truncated"] is True
    assert len(files["keep.py"]["a"]["patch"]) == 40
    assert files["keep.py"]["a"]["hunks"]


def test_pair_diff_demo_mode_and_unknown_branch(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(verifier, "DEMO_ROOT", tmp_path / "bundled")
    client = TestClient(app)
    denied = client.post(
        "/api/pair-diff",
        json={"repo_path": str(repo), "branch_a": "feat-a", "branch_b": "feat-b"},
    )
    assert denied.status_code == 403
    assert "repo_url" in denied.json()["detail"]

    url = client.post(
        "/api/pair-diff",
        json={
            "repo_url": "https://github.com/example/huge.git",
            "branch_a": "a",
            "branch_b": "b",
        },
    )
    assert url.status_code == 403

    monkeypatch.setattr(settings, "demo_mode", False)
    missing = client.post(
        "/api/pair-diff",
        json={"repo_path": str(repo), "branch_a": "feat-a", "branch_b": "no-such"},
    )
    assert missing.status_code == 400
    assert "merge-base" in missing.json()["detail"] or "no-such" in missing.json()["detail"]

    dashed = client.post(
        "/api/pair-diff",
        json={"repo_path": str(repo), "branch_a": "--upload-pack=x", "branch_b": "feat-b"},
    )
    assert dashed.status_code == 400
    assert "invalid revision" in dashed.json()["detail"]


def test_pair_diff_uses_the_same_repo_resolution_as_analyze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-PR URL clone is diffed by origin/* names, same as analyze."""
    monkeypatch.setattr(settings, "github_token", "")
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
    _init(source)
    (source / "app.py").write_text("base\n")
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "base")
    _git(source, "remote", "add", "origin", str(remote))
    _git(source, "push", "-q", "origin", "main")
    _git(source, "checkout", "-q", "-b", "feat-a")
    (source / "app.py").write_text("from a\n")
    _git(source, "commit", "-qam", "a")
    _git(source, "push", "-q", "origin", "feat-a")
    _git(source, "checkout", "-q", "main")
    _git(source, "checkout", "-q", "-b", "feat-b")
    (source / "app.py").write_text("from b\n")
    _git(source, "commit", "-qam", "b")
    _git(source, "push", "-q", "origin", "feat-b")

    response = TestClient(app).post(
        "/api/pair-diff",
        json={
            "repo_url": str(remote),
            "base_branch": "main",
            "branch_a": "origin/feat-a",
            "branch_b": "origin/feat-b",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["files"][0]["path"] == "app.py"
    assert body["files"][0]["shared"] is True
    assert body["files"][0]["overlapping"] is True
    assert "from a" in body["files"][0]["a"]["patch"]
