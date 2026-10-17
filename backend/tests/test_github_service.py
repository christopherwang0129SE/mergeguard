from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.api import routes
from app.schemas import AnalyzeResponse, GitHubPullRequest
from app.services import git_service, github_service


def _pull(number: int, author: str, base: str = "main") -> dict[str, Any]:
    return {
        "number": number,
        "title": f"Pull request {number}",
        "html_url": f"https://github.com/example/project/pull/{number}",
        "updated_at": "2026-10-04T10:00:00Z",
        "user": {"login": author},
        "head": {"ref": f"feature-{number}"},
        "base": {"ref": base},
    }


def test_lists_open_pulls_only_for_seated_authors_and_configured_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, dict[str, Any]]] = []

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[dict[str, Any]]:
            return [
                _pull(1, "SEATED"),
                _pull(2, "visitor"),
                _pull(3, "seated", base="release"),
            ]

    class Client:
        def __init__(self, *, timeout: float, headers: dict[str, str]) -> None:
            assert timeout == 15.0
            assert headers["Authorization"] == "Bearer secret"

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get(self, url: str, *, params: dict[str, Any]) -> Response:
            requests.append((url, params))
            return Response()

    monkeypatch.setattr(github_service.httpx, "Client", Client)
    pulls = github_service.list_open_pull_requests(
        "example/project",
        {"seated"},
        "main",
        token="secret",
    )

    assert [pull.number for pull in pulls] == [1]
    assert pulls[0].author == "SEATED"
    assert requests == [
        (
            "https://api.github.com/repos/example/project/pulls",
            {"state": "open", "base": "main", "per_page": 100, "page": 1},
        )
    ]


def test_paginates_all_open_pull_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    pages: list[int] = []

    class Response:
        def __init__(self, page: int) -> None:
            self.page = page

        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[dict[str, Any]]:
            if self.page == 1:
                return [_pull(number, "seated") for number in range(1, 101)]
            return [_pull(101, "seated")]

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get(self, url: str, *, params: dict[str, Any]) -> Response:
            pages.append(params["page"])
            return Response(params["page"])

    monkeypatch.setattr(github_service.httpx, "Client", Client)
    pulls = github_service.list_open_pull_requests("example/project", {"seated"}, "main")

    assert len(pulls) == 101
    assert pages == [1, 2]


@pytest.mark.parametrize(
    "repository",
    ["", "../project", "example/project/extra", "https://github.com/example/project"],
)
def test_rejects_invalid_repository_names(repository: str) -> None:
    with pytest.raises(github_service.GitHubError):
        github_service.validate_repository(repository)


def test_requires_seated_user_configuration() -> None:
    with pytest.raises(github_service.GitHubError, match="seated GitHub username"):
        github_service.list_open_pull_requests("example/project", set(), "main")


def test_fetches_and_diffs_github_pull_request_ref(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
    source.mkdir()
    subprocess.run(["git", "-C", str(source), "init", "-q", "-b", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "file.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "base"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "remote", "add", "origin", str(remote)],
        check=True,
    )
    subprocess.run(["git", "-C", str(source), "push", "-q", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(source), "checkout", "-q", "-b", "feature"], check=True)
    (source / "file.txt").write_text("pull request change\n")
    subprocess.run(["git", "-C", str(source), "commit", "-qam", "change"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "push", "-q", "origin", "HEAD:refs/pull/7/head"],
        check=True,
    )

    git_service.clone(str(remote), clone)
    branch = git_service.fetch_pull_request(clone, 7)
    diff = git_service.diff_against_base(clone, branch, "main")

    assert branch == "mergeguard/pr/7"
    assert [changed.path for changed in diff.files] == ["file.txt"]
    assert diff.files[0].added_lines == 1


def test_sync_endpoint_returns_pull_requests_and_risk_analysis(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pull = GitHubPullRequest(
        number=12,
        title="Feature",
        author="seated",
        url="https://github.com/example/project/pull/12",
        head_branch="feature",
        base_branch="main",
        updated_at="2026-10-04T10:00:00Z",
    )
    analysis = AnalyzeResponse(
        base_branch="origin/main",
        branches=["mergeguard/pr/12"],
        diffs=[],
        pairs=[],
    )
    monkeypatch.setattr(routes.settings, "github_repository", "example/project")
    monkeypatch.setattr(routes.settings, "github_seated_users", "seated")
    monkeypatch.setattr(routes.settings, "github_token", "")
    monkeypatch.setattr(routes.settings, "default_base_branch", "main")
    monkeypatch.setattr(routes.settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(
        routes.github_service,
        "list_open_pull_requests",
        lambda **kwargs: [pull],
    )
    monkeypatch.setattr(routes.git_service, "clone", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(
        routes.git_service,
        "fetch_pull_request",
        lambda *args, **kwargs: "mergeguard/pr/12",
    )
    monkeypatch.setattr(routes.sentinel, "analyze", lambda *args, **kwargs: analysis)

    result = routes.sync_github_pull_requests()

    assert result.repository == "example/project"
    assert result.pull_requests == [pull]
    assert result.analysis == analysis
