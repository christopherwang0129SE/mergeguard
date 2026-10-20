"""GitHub repo URLs, pull request URLs, and remote-branch listing."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.config import settings
from app.main import app
from app.schemas import GitHubPullRequest
from app.services import git_service, github_service


_TOKEN = "ghp_SUPERSECRETTOKENVALUE"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")


def _pull(number: int, base: str = "main") -> GitHubPullRequest:
    return GitHubPullRequest(
        number=number,
        title=f"Pull {number}",
        author="octocat",
        url=f"https://github.com/acme/widgets/pull/{number}",
        head_branch=f"feature-{number}",
        base_branch=base,
        updated_at="2026-10-04T10:00:00Z",
    )


def _bare_repo(tmp_path: Path, *, pulls: range | None = None) -> Path:
    """Bare repo with main, feat-a, feat-b, and optional refs/pull/N/head."""
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
    _init(source)
    (source / "app.py").write_text("base\n")
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "base")
    _git(source, "remote", "add", "origin", str(remote))
    _git(source, "push", "-q", "origin", "main")
    for name, text in (("feat-a", "branch a\n"), ("feat-b", "branch b\n")):
        _git(source, "checkout", "-q", "main")
        _git(source, "checkout", "-q", "-b", name)
        (source / "app.py").write_text(text)
        _git(source, "commit", "-qam", name)
        _git(source, "push", "-q", "origin", name)
    if pulls is not None:
        for number in pulls:
            _git(source, "checkout", "-q", "main")
            _git(source, "checkout", "-q", "-b", f"pr-src-{number}")
            (source / "app.py").write_text(f"pull {number}\n")
            _git(source, "commit", "-qam", f"pr {number}")
            _git(source, "push", "-q", "origin", f"HEAD:refs/pull/{number}/head")
    return remote


def test_parse_github_repo_and_pull_urls() -> None:
    plain = github_service.parse_github_url("https://github.com/o/r")
    dotted = github_service.parse_github_url("https://github.com/o/r.git")
    slashed = github_service.parse_github_url("https://github.com/o/my.repo/")
    pull = github_service.parse_github_url("https://github.com/o/r/pull/12")
    pull_slash = github_service.parse_github_url("https://github.com/o/r/pull/12/")
    assert plain is not None and plain.repository == "o/r" and plain.number is None
    assert dotted is not None and dotted.repository == "o/r" and dotted.clone_url.endswith("/r.git")
    assert slashed is not None and slashed.repo == "my.repo"
    assert pull is not None and pull.number == 12 and pull.clone_url == "https://github.com/o/r.git"
    assert pull_slash is not None and pull_slash.number == 12
    assert github_service.parse_github_url("https://gitlab.com/o/r") is None
    assert github_service.parse_github_url("https://github.com/o/r/issues/3") is None
    assert github_service.parse_github_url("https://github.com/../r") is None


def test_url_clone_lists_remote_branches_and_scores_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "github_token", "")
    remote = _bare_repo(tmp_path)
    response = TestClient(app).post(
        "/api/analyze",
        json={"repo_url": str(remote), "base_branch": "main", "verify": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["base_branch"] == "main"
    assert body["branches"] == ["origin/feat-a", "origin/feat-b"]
    assert len(body["pairs"]) == 1
    assert body["pairs"][0]["shared_files"] == 1


def test_github_repo_urls_are_cloned_as_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "github_token", "")
    remote = _bare_repo(tmp_path)
    real_clone = git_service.clone
    seen: list[str] = []

    def fake_clone(url: str, dest: Path, token: str | None = None) -> Path:
        seen.append(url)
        assert token in (None, "")
        assert _TOKEN not in url
        return real_clone(str(remote), dest)

    monkeypatch.setattr(routes.git_service, "clone", fake_clone)

    def forbid_github(*_args, **_kwargs):
        raise AssertionError("a plain repo URL must not call the GitHub API")

    monkeypatch.setattr(routes.github_service, "get_pull_request", forbid_github)
    client = TestClient(app)
    for url in ("https://github.com/o/r", "https://github.com/o/r.git"):
        seen.clear()
        response = client.post("/api/analyze", json={"repo_url": url, "verify": False})
        assert response.status_code == 200, response.text
        assert seen == [url]
        assert response.json()["branches"] == ["origin/feat-a", "origin/feat-b"]
        assert response.json()["pairs"]


def test_pull_request_url_fetches_pr_heads_on_the_pr_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "github_token", "")
    remote = _bare_repo(tmp_path, pulls=range(1, 10))
    real_clone = git_service.clone
    fetched: list[int] = []

    def fake_clone(url: str, dest: Path, token: str | None = None) -> Path:
        assert url == "https://github.com/acme/widgets.git"
        assert token in (None, "")
        return real_clone(str(remote), dest)

    def fake_get(repository: str, number: int, token: str | None = None) -> GitHubPullRequest:
        assert repository == "acme/widgets"
        assert number == 2
        assert token in (None, "")
        return _pull(2, base="main")

    def fake_list(repository: str, base_branch: str, token: str | None = None, *, limit: int | None = None):
        assert repository == "acme/widgets"
        assert base_branch == "main"
        assert limit == routes._MAX_OTHER_PULLS + 1
        return [_pull(number) for number in range(1, 21)]

    real_fetch = git_service.fetch_pull_request

    def spy_fetch(repo: Path, number: int, token: str | None = None, *, local_branch: str | None = None):
        fetched.append(number)
        assert local_branch == f"pr-{number}"
        assert token in (None, "")
        return real_fetch(repo, number, token=token, local_branch=local_branch)

    monkeypatch.setattr(routes.git_service, "clone", fake_clone)
    monkeypatch.setattr(routes.git_service, "fetch_pull_request", spy_fetch)
    monkeypatch.setattr(routes.github_service, "get_pull_request", fake_get)
    monkeypatch.setattr(routes.github_service, "list_open_pulls", fake_list)

    response = TestClient(app).post(
        "/api/analyze",
        json={
            "repo_url": "https://github.com/acme/widgets/pull/2",
            "base_branch": "some-other-base",
            "verify": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["base_branch"] == "main"
    assert body["branches"] == [
        "pr-2",
        "pr-1",
        "pr-3",
        "pr-4",
        "pr-5",
        "pr-6",
        "pr-7",
        "pr-8",
        "pr-9",
    ]
    assert fetched == [2, 1, 3, 4, 5, 6, 7, 8, 9]
    assert len(body["pairs"]) == 36
    assert _TOKEN not in response.text


def test_github_api_failure_is_400_before_clone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "github_token", "")
    cloned = {"n": 0}

    def fake_clone(*_args, **_kwargs):
        cloned["n"] += 1
        raise AssertionError("clone should not run")

    def fake_get(*_args, **_kwargs):
        raise github_service.GitHubError("GitHub API returned HTTP 404")

    monkeypatch.setattr(routes.git_service, "clone", fake_clone)
    monkeypatch.setattr(routes.github_service, "get_pull_request", fake_get)
    response = TestClient(app).post(
        "/api/analyze",
        json={"repo_url": "https://github.com/acme/widgets/pull/9"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub API returned HTTP 404"
    assert cloned["n"] == 0


def test_token_never_appears_in_clone_args_or_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "github_token", _TOKEN)

    def fake_run(args, **kwargs):
        joined = " ".join(args)
        assert _TOKEN not in joined
        env = kwargs.get("env") or {}
        for value in env.values():
            assert _TOKEN not in str(value)

        class Proc:
            returncode = 1
            stderr = f"fatal: authentication failed for {_TOKEN}"
            stdout = ""

        return Proc()

    monkeypatch.setattr(git_service.subprocess, "run", fake_run)
    with pytest.raises(git_service.GitError) as exc:
        git_service.clone("https://github.com/acme/widgets.git", tmp_path / "dest", token=_TOKEN)
    assert _TOKEN not in str(exc.value)
    assert "[redacted]" in str(exc.value)

    def leaky_clone(url, dest, token=None):
        raise git_service.GitError(f"clone failed: {token} {url}")

    monkeypatch.setattr(routes.git_service, "clone", leaky_clone)
    response = TestClient(app).post(
        "/api/analyze/stream",
        json={"repo_url": f"https://user:{_TOKEN}@github.com/acme/widgets.git"},
    )
    assert response.status_code == 400
    assert _TOKEN not in response.text
    assert "application/json" in response.headers["content-type"]


def test_userinfo_is_stripped_before_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str | None]] = []

    def fake_clone(url, dest, token=None):
        seen.append((url, token))
        raise git_service.GitError(f"clone failed: {url}")

    monkeypatch.setattr(settings, "github_token", "")
    monkeypatch.setattr(routes.git_service, "clone", fake_clone)
    response = TestClient(app).post(
        "/api/analyze",
        json={"repo_url": f"https://x-access-token:{_TOKEN}@github.com/acme/widgets.git"},
    )
    assert response.status_code == 400
    assert seen == [("https://github.com/acme/widgets.git", None)]
    assert _TOKEN not in response.text


def test_list_open_pulls_stops_at_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    pages: list[int] = []

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return [
                {
                    "number": number,
                    "title": f"Pull {number}",
                    "html_url": f"https://github.com/acme/widgets/pull/{number}",
                    "updated_at": "2026-10-04T10:00:00Z",
                    "user": {"login": "octocat"},
                    "head": {"ref": f"feature-{number}"},
                    "base": {"ref": "main"},
                }
                for number in range(1, 101)
            ]

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_args) -> None:
            pass

        def get(self, url: str, *, params: dict) -> Response:
            pages.append(params["page"])
            assert "Authorization" not in Client.headers
            return Response()

        headers: dict = {}

        def __init_subclass__(cls) -> None:
            return None

    def capturing_client(*, timeout: float, headers: dict):
        assert timeout == 15.0
        Client.headers = headers
        return Client()

    monkeypatch.setattr(github_service.httpx, "Client", capturing_client)
    pulls = github_service.list_open_pulls("acme/widgets", "main", limit=9)
    assert [pull.number for pull in pulls] == list(range(1, 10))
    assert pages == [1]


def test_get_pull_request_reads_one_record(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {
                "number": 4,
                "title": "Fix",
                "html_url": "https://github.com/acme/widgets/pull/4",
                "updated_at": "2026-10-04T10:00:00Z",
                "user": {"login": "octocat"},
                "head": {"ref": "feature-4"},
                "base": {"ref": "develop"},
            }

    class Client:
        def __init__(self, *, timeout: float, headers: dict) -> None:
            assert timeout == 15.0
            assert "Authorization" not in headers

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_args) -> None:
            pass

        def get(self, url: str) -> Response:
            assert url == "https://api.github.com/repos/acme/widgets/pulls/4"
            return Response()

    monkeypatch.setattr(github_service.httpx, "Client", Client)
    pull = github_service.get_pull_request("acme/widgets", 4)
    assert pull.number == 4
    assert pull.base_branch == "develop"
    assert pull.head_branch == "feature-4"
