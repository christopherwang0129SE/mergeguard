"""Replay mined cases onto local main/a/b branches. No network."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.bench.replay import _at_pr_head, build_replay, fetch_pr_head, main, parse_declared_deps

_AUTHOR = "MergeGuard Bench <bench@mergeguard.local>"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def _init(tmp: Path) -> Path:
    repo = tmp / "src"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit(repo: Path, subject: str, files: dict[str, str]) -> str:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD").strip()


def _case(
    case_id: str,
    parent: str,
    a_sha: str,
    a_kind: str,
    b_sha: str,
    b_kind: str,
    b_parent: str | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "repo": "src",
        "label": "broke",
        "gt_kind": "revert",
        "confidence": "revert",
        "fix_sha": None,
        "fix_subject": None,
        "pair_link": "shared-file",
        "a": {
            "sha": a_sha,
            "parent": parent,
            "kind": a_kind,
            "pr": 42 if a_kind == "squash" else None,
            "subject": "change A",
            "files": [],
        },
        "b": {
            "sha": b_sha,
            "parent": b_parent or parent,
            "kind": b_kind,
            "pr": None,
            "subject": "change B",
            "files": [],
        },
    }


def _show(repo: Path, rev: str, name: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{rev}:{name}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _heads(repo: Path) -> set[str]:
    return set(_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())


def _assert_no_remote(repo: Path) -> None:
    assert _git(repo, "remote").strip() == ""
    proc = subprocess.run(
        ["git", "-C", str(repo), "config", "--get-regexp", r"^remote\."],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0


def _assert_alternates(replay: Path, src: Path) -> None:
    alternates = replay / ".git" / "objects" / "info" / "alternates"
    assert alternates.is_file()
    assert str((src / ".git" / "objects").resolve()) in alternates.read_text(encoding="utf-8")


def test_squash_and_direct_replay_contents_remote_and_reuse(tmp_path: Path, capsys) -> None:
    src = _init(tmp_path)
    base = _commit(src, "base", {"app.py": "base\n", "readme.md": "r\n"})
    a_sha = _commit(src, "Add widget (#42)", {"app.py": "from-a\n", "widget.py": "w\n"})
    _git(src, "checkout", "-q", "-B", "side", base)
    b_sha = _commit(src, "Add notes", {"notes.py": "n\n"})
    case = _case("demo-direct", base, a_sha, "squash", b_sha, "direct")
    root = tmp_path / "replay"

    meta = build_replay(src, case, root)
    replay = Path(meta["path"])

    assert meta["main"] == base
    assert meta["a_apply_failed"] is False and meta["b_apply_failed"] is False
    assert meta["a_method"] == "cherry-pick" and meta["b_method"] == "cherry-pick"
    assert meta["textual_conflict"] is False
    assert _show(replay, "main", "app.py") == "base\n"
    assert _show(replay, "a", "app.py") == "from-a\n"
    assert _show(replay, "a", "widget.py") == "w\n"
    assert _show(replay, "a", "readme.md") == "r\n"
    assert _show(replay, "a", "notes.py") is None
    assert _show(replay, "b", "notes.py") == "n\n"
    assert _show(replay, "b", "app.py") == "base\n"
    assert _show(replay, "b", "widget.py") is None
    assert _heads(replay) == {"main", "a", "b"}
    assert _git(replay, "log", "-1", "--format=%an <%ae>", "a").strip() == _AUTHOR
    assert _git(replay, "log", "-1", "--format=%cn <%ce>", "b").strip() == _AUTHOR
    _assert_alternates(replay, src)
    _assert_no_remote(replay)

    marker = replay / "KEEP"
    marker.write_text("x")
    again = build_replay(src, case, root)
    assert again == meta
    assert marker.read_text() == "x"

    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps(case) + "\n")
    assert main(["--cases", str(cases), "--src", str(src), "--root", str(root), "--limit", "1"]) == 0
    line = capsys.readouterr().out.strip()
    assert "\n" not in line
    assert json.loads(line)["a"] == meta["a"]
    _assert_no_remote(replay)


def test_merge_commit_replay(tmp_path: Path) -> None:
    src = _init(tmp_path)
    base = _commit(src, "base", {"a.py": "base\n", "keep.py": "keep\n"})
    _git(src, "checkout", "-q", "-b", "feature")
    _commit(src, "feature work", {"a.py": "feat\n", "extra.py": "e\n"})
    _git(src, "checkout", "-q", "main")
    _git(src, "merge", "--no-ff", "-m", "Merge pull request #5 from dev/feature", "feature")
    merge_sha = _git(src, "rev-parse", "HEAD").strip()
    _git(src, "checkout", "-q", "-B", "side", base)
    b_sha = _commit(src, "side notes", {"other.py": "o\n"})
    case = _case("demo-merge", base, merge_sha, "merge", b_sha, "direct")

    meta = build_replay(src, case, tmp_path / "replay")
    replay = Path(meta["path"])

    assert meta["a_apply_failed"] is False and meta["b_apply_failed"] is False
    assert meta["a_method"] == "apply-3way" and meta["b_method"] == "cherry-pick"
    assert meta["textual_conflict"] is False
    assert _show(replay, "a", "a.py") == "feat\n"
    assert _show(replay, "a", "extra.py") == "e\n"
    assert _show(replay, "a", "keep.py") == "keep\n"
    assert _show(replay, "a", "other.py") is None
    assert _show(replay, "b", "other.py") == "o\n"
    assert _show(replay, "b", "a.py") == "base\n"
    assert _show(replay, "main", "a.py") == "base\n"
    assert _heads(replay) == {"main", "a", "b"}
    _assert_no_remote(replay)


def test_conflicting_b_omits_branch(tmp_path: Path) -> None:
    src = _init(tmp_path)
    base = _commit(src, "base", {"shared.py": "base\n"})
    a_sha = _commit(src, "edit A", {"shared.py": "AAA\n"})
    b_sha = _commit(src, "edit B", {"shared.py": "BBB\n"})
    case = _case("demo-conflict", base, a_sha, "direct", b_sha, "direct", b_parent=a_sha)
    root = tmp_path / "replay"

    meta = build_replay(src, case, root)
    replay = Path(meta["path"])

    assert meta["a_apply_failed"] is False
    assert meta["b_apply_failed"] is True
    assert meta["b"] is None
    assert meta["a_method"] == "cherry-pick" and meta["b_method"] is None
    assert meta["textual_conflict"] is False
    assert _show(replay, "a", "shared.py") == "AAA\n"
    assert _heads(replay) == {"main", "a"}
    _assert_no_remote(replay)

    marker = replay / "KEEP"
    marker.write_text("x")
    again = build_replay(src, case, root, rebuild_if_b_missing=False)
    assert again["b_apply_failed"] is True and again["a"] == meta["a"]
    assert marker.read_text() == "x"
    assert _heads(replay) == {"main", "a"}

    rebuilt = build_replay(src, case, root)
    assert rebuilt["b_apply_failed"] is True and rebuilt["b_method"] is None
    assert not marker.exists()
    assert _heads(replay) == {"main", "a"}


def test_textual_conflict_when_both_apply(tmp_path: Path) -> None:
    src = _init(tmp_path)
    base = _commit(src, "base", {"f.py": "line\n"})
    a_sha = _commit(src, "from A", {"f.py": "A\n"})
    _git(src, "checkout", "-q", "-B", "side", base)
    b_sha = _commit(src, "from B", {"f.py": "B\n"})
    case = _case("demo-overlap", base, a_sha, "direct", b_sha, "squash")

    meta = build_replay(src, case, tmp_path / "replay")
    replay = Path(meta["path"])

    assert meta["a_apply_failed"] is False and meta["b_apply_failed"] is False
    assert meta["a_method"] == "cherry-pick" and meta["b_method"] == "cherry-pick"
    assert meta["textual_conflict"] is True
    assert _show(replay, "a", "f.py") == "A\n"
    assert _show(replay, "b", "f.py") == "B\n"
    _assert_no_remote(replay)


def test_parse_declared_deps_manifests() -> None:
    manifests = {
        "requirements.txt": "\n".join(
            [
                "# comment",
                "requests==2.31.0",
                "-e .",
                "-r requirements-dev.txt",
                "--index-url https://example.invalid/simple",
                "rich>=13",
                "wheel==0.43.0 --hash=sha256:abc",
                "pytest>=8 ; python_version >= '3.11'",
                ".",
            ]
        ),
        "requirements-dev.txt": "black==24.1.0\n-e ./local\n",
        "requirements/nested.txt": "numpy\n",
        "notes.txt": "ignored\n",
        "setup.py": "install_requires=['numpy']\n",
        "pyproject.toml": """
[project]
name = "widget"
dependencies = ["flask>=2", "requests>=2"]

[project.optional-dependencies]
dev = ["ruff"]
test = ["coverage>=7"]
tests = ["pytest-cov"]
testing = ["hypothesis"]
docs = ["sphinx"]
lint = ["mypy"]

[build-system]
requires = ["setuptools"]
""",
    }
    assert parse_declared_deps(manifests) == [
        "black==24.1.0",
        "requests==2.31.0",
        "rich>=13",
        "wheel==0.43.0",
        "pytest>=8 ; python_version >= '3.11'",
        "flask>=2",
        "requests>=2",
        "coverage>=7",
        "pytest-cov",
        "hypothesis",
        "ruff",
    ]
    assert parse_declared_deps(
        {"pyproject.toml": "this is not [", "requirements.txt": "click\n"}
    ) == ["click"]


def _origin(tmp: Path) -> Path:
    origin = tmp / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@example.com")
    _git(origin, "config", "user.name", "Test")
    return origin


def test_squash_conflict_uses_pr_head_from_local_origin(tmp_path: Path) -> None:
    origin = _origin(tmp_path)
    base = _commit(origin, "base", {"shared.py": "line1\nline2\n"})
    _git(origin, "checkout", "-q", "-B", "pr", base)
    pr_sha = _commit(origin, "pr work", {"shared.py": "BBB\nline2\n"})
    _git(origin, "update-ref", "refs/pull/7/head", pr_sha)
    _git(origin, "checkout", "-q", "main")
    _git(origin, "branch", "-D", "pr")
    a_sha = _commit(origin, "change A", {"shared.py": "AAA\nline2\n"})
    b_sha = _commit(origin, "change B (#7)", {"shared.py": "BBB\nline2\n"})
    src = tmp_path / "src"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(src)], check=True)
    _git(src, "remote", "set-url", "--push", "origin", "DISABLED")
    fetch_url = _git(src, "remote", "get-url", "origin").strip()
    case = _case("demo-pr-head", base, a_sha, "squash", b_sha, "squash", b_parent=a_sha)
    case["repo"] = "acme__widget"
    case["b"]["pr"] = 7

    meta = build_replay(src, case, tmp_path / "replay")
    replay = Path(meta["path"])

    assert meta["b_apply_failed"] is False and meta["b_method"] == "pr-head"
    assert meta["a_empty"] is False and meta["b_empty"] is False
    assert meta["a_method"] == "cherry-pick" and meta["a_apply_failed"] is False
    assert meta["b"] == pr_sha
    assert _show(replay, "b", "shared.py") == "BBB\nline2\n"
    assert _show(replay, "a", "shared.py") == "AAA\nline2\n"
    assert _show(replay, "main", "shared.py") == "line1\nline2\n"
    assert _heads(replay) == {"main", "a", "b"}
    _assert_no_remote(replay)
    assert _git(src, "rev-parse", "refs/bench/pr/7").strip() == pr_sha
    assert _git(src, "remote", "get-url", "origin").strip() == fetch_url
    assert _git(src, "config", "--get", "remote.origin.pushurl").strip() == "DISABLED"

    _git(src, "remote", "set-url", "origin", str(tmp_path / "missing-origin"))
    assert fetch_pr_head(src, 7) == pr_sha
    assert build_replay(src, case, tmp_path / "replay") == meta


def test_pr_head_rejected_without_shared_history(tmp_path: Path) -> None:
    origin = _origin(tmp_path)
    base = _commit(origin, "base", {"shared.py": "line1\n"})
    a_sha = _commit(origin, "change A", {"shared.py": "AAA\n"})
    b_sha = _commit(origin, "change B", {"shared.py": "BBB\n"})
    _git(origin, "checkout", "-q", "--orphan", "side")
    orphan = _commit(origin, "unrelated", {"shared.py": "ZZZ\n"})
    _git(origin, "update-ref", "refs/pull/8/head", orphan)
    _git(origin, "checkout", "-q", "main")
    _git(origin, "branch", "-D", "side")
    src = tmp_path / "src"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(src)], check=True)
    case = _case("demo-unrelated", base, a_sha, "direct", b_sha, "direct", b_parent=a_sha)
    case["repo"] = "acme/widget"
    case["b"]["pr"] = 8

    meta = build_replay(src, case, tmp_path / "replay")
    replay = Path(meta["path"])

    assert meta["b_apply_failed"] is True and meta["b"] is None and meta["b_method"] is None
    assert meta["a_method"] == "cherry-pick"
    assert _git(src, "rev-parse", "refs/bench/pr/8").strip() == orphan
    assert _heads(replay) == {"main", "a"}
    _assert_no_remote(replay)


def test_pr_fetch_failure_and_missing_owner_omit_b(tmp_path: Path, monkeypatch) -> None:
    src = _init(tmp_path)
    base = _commit(src, "base", {"shared.py": "line1\n"})
    a_sha = _commit(src, "A", {"shared.py": "AAA\n"})
    b_sha = _commit(src, "B", {"shared.py": "BBB\n"})
    case = _case("demo-fetch-fail", base, a_sha, "direct", b_sha, "direct", b_parent=a_sha)
    case["repo"] = "acme__widget"
    case["b"]["pr"] = 7
    meta = build_replay(src, case, tmp_path / "replay", fetch_pr=lambda _repo, _pr: None)
    assert meta["b_apply_failed"] is True and meta["b_method"] is None
    assert meta["a_method"] == "cherry-pick"

    calls: list[object] = []
    monkeypatch.setattr(
        "scripts.bench.replay.fetch_pr_head",
        lambda *args, **kwargs: calls.append(args),
    )
    case["case_id"] = "demo-no-owner"
    case["repo"] = "src"
    missed = build_replay(src, case, tmp_path / "replay2")
    assert calls == []
    assert missed["b_apply_failed"] is True and missed["b_method"] is None


def test_pr_head_fetched_into_replay_when_object_is_absent(tmp_path: Path) -> None:
    src = _init(tmp_path)
    base = _commit(src, "base", {"f.py": "base\n"})
    replay = tmp_path / "replay"
    subprocess.run(["git", "clone", "--quiet", str(src), str(replay)], check=True)
    pr_sha = _commit(src, "pr", {"f.py": "from-pr\n"})
    _git(src, "update-ref", "refs/bench/pr/3", pr_sha)
    _git(src, "checkout", "-q", "-B", "main", base)
    missing = subprocess.run(
        ["git", "-C", str(replay), "cat-file", "-e", f"{pr_sha}^{{commit}}"],
        capture_output=True,
    )
    assert missing.returncode != 0
    assert _at_pr_head(replay, src, "b", pr_sha, 3) is True
    assert _git(replay, "rev-parse", "b").strip() == pr_sha
    assert _show(replay, "b", "f.py") == "from-pr\n"


def test_at_pr_head_rejects_ancestor_of_a_and_empty_diff(tmp_path: Path) -> None:
    src = _init(tmp_path)
    base = _commit(src, "base", {"f.py": "base\n"})
    a_sha = _commit(src, "A", {"f.py": "from-a\n"})
    contains = _commit(src, "pr on A", {"f.py": "from-a\n", "g.py": "g\n"})
    _git(src, "checkout", "-q", "-B", "main", base)
    _git(src, "commit", "--allow-empty", "-q", "-m", "empty pr")
    empty = _git(src, "rev-parse", "HEAD").strip()
    _git(src, "checkout", "-q", "-B", "main", base)
    parallel = _commit(src, "parallel", {"f.py": "from-pr\n"})
    _git(src, "checkout", "-q", "-B", "main", base)
    for number, sha in ((1, contains), (2, empty), (3, parallel)):
        _git(src, "update-ref", f"refs/bench/pr/{number}", sha)

    replay = tmp_path / "replay"
    subprocess.run(
        ["git", "clone", "--quiet", "--shared", "--no-checkout", str(src), str(replay)],
        check=True,
    )
    _git(replay, "checkout", "-q", "-B", "main", base)

    assert _at_pr_head(replay, src, "b", contains, 1, a_sha=a_sha) is False
    assert "b" not in _heads(replay)
    assert _at_pr_head(replay, src, "b", empty, 2, a_sha=a_sha) is False
    assert "b" not in _heads(replay)
    assert _at_pr_head(replay, src, "b", parallel, 3, a_sha=a_sha) is True
    assert _git(replay, "rev-parse", "b").strip() == parallel

    _git(replay, "branch", "-D", "b")
    unknown = "0123456789abcdef" * 2 + "01234567"
    assert _at_pr_head(replay, src, "b", contains, 1, a_sha=unknown) is True
    assert _git(replay, "rev-parse", "b").strip() == contains


def _conflicting_b(src: Path, base: str, a_sha: str) -> str:
    _git(src, "checkout", "-q", "-B", "side", a_sha)
    b_sha = _commit(src, "change B", {"shared.py": "BBB\nline2\n"})
    _git(src, "checkout", "-q", "-B", "main", base)
    return b_sha


def test_build_replay_rejects_pr_head_that_contains_a(tmp_path: Path) -> None:
    src = _init(tmp_path)
    base = _commit(src, "base", {"shared.py": "line1\nline2\n"})
    a_sha = _commit(src, "change A", {"shared.py": "AAA\nline2\n"})
    pr_sha = _commit(src, "pr after A", {"shared.py": "AAA\nfrom-pr\n"})
    _git(src, "update-ref", "refs/bench/pr/7", pr_sha)
    b_sha = _conflicting_b(src, base, a_sha)
    case = _case("contains-a", base, a_sha, "direct", b_sha, "direct", b_parent=a_sha)
    case["repo"] = "acme__widget"
    case["b"]["pr"] = 7
    _git(src, "merge-base", "--is-ancestor", a_sha, pr_sha)
    differed = subprocess.run(
        ["git", "-C", str(src), "diff", "--quiet", base, pr_sha], capture_output=True,
    )
    assert differed.returncode == 1

    meta = build_replay(src, case, tmp_path / "replay")

    assert meta["a_method"] == "cherry-pick" and meta["a_apply_failed"] is False
    assert meta["b_apply_failed"] is True and meta["b"] is None and meta["b_method"] is None
    assert meta["b_empty"] is False
    assert _heads(Path(meta["path"])) == {"main", "a"}


def test_build_replay_rejects_pr_head_with_no_diff(tmp_path: Path) -> None:
    src = _init(tmp_path)
    base = _commit(src, "base", {"shared.py": "line1\nline2\n"})
    _git(src, "commit", "--allow-empty", "-q", "-m", "empty pr")
    pr_sha = _git(src, "rev-parse", "HEAD").strip()
    _git(src, "update-ref", "refs/bench/pr/9", pr_sha)
    _git(src, "checkout", "-q", "-B", "main", base)
    a_sha = _commit(src, "change A", {"shared.py": "AAA\nline2\n"})
    b_sha = _conflicting_b(src, base, a_sha)
    ancestor = subprocess.run(
        ["git", "-C", str(src), "merge-base", "--is-ancestor", a_sha, pr_sha],
        capture_output=True,
    )
    assert ancestor.returncode != 0
    case = _case("empty-pr", base, a_sha, "direct", b_sha, "direct", b_parent=a_sha)
    case["repo"] = "acme__widget"
    case["b"]["pr"] = 9

    meta = build_replay(src, case, tmp_path / "replay")

    assert meta["a_method"] == "cherry-pick" and meta["a_apply_failed"] is False
    assert meta["b_apply_failed"] is True and meta["b"] is None and meta["b_method"] is None
    assert _heads(Path(meta["path"])) == {"main", "a"}


def test_meta_reports_empty_branch_when_replay_is_reused(tmp_path: Path) -> None:
    src = _init(tmp_path)
    base = _commit(src, "base", {"app.py": "base\n"})
    a_sha = _commit(src, "A", {"app.py": "from-a\n"})
    _git(src, "checkout", "-q", "-B", "side", base)
    b_sha = _commit(src, "B", {"notes.py": "n\n"})
    case = _case("empty-meta", base, a_sha, "direct", b_sha, "direct")
    root = tmp_path / "replay"

    meta = build_replay(src, case, root)
    replay = Path(meta["path"])
    assert meta["a_empty"] is False and meta["b_empty"] is False
    assert meta["a"] != meta["main"] and meta["b"] != meta["main"]

    _git(replay, "branch", "-f", "b", "main")
    again = build_replay(src, case, root)
    assert again["b"] == meta["main"]
    assert again["b_empty"] is True and again["a_empty"] is False
    assert again["b_apply_failed"] is False
    assert again["path"] == meta["path"]

    _git(replay, "branch", "-f", "a", "main")
    both = build_replay(src, case, root)
    assert both["a_empty"] is True and both["b_empty"] is True

    _git(replay, "branch", "-D", "b")
    missing = build_replay(src, case, root, rebuild_if_b_missing=False)
    assert missing["b"] is None and missing["b_empty"] is False
