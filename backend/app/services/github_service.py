"""Read and filter open pull requests from a configured GitHub repository."""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from app.schemas import GitHubPullRequest
from app.services.git_service import redact_secrets

_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_API_URL = "https://api.github.com"
_PR_URL_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>[1-9]\d*)/?$"
)
_REPO_URL_RE = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class GitHubError(RuntimeError):
    pass


def validate_repository(repository: str) -> str:
    normalized = repository.strip().removesuffix(".git")
    if (
        not _REPOSITORY_RE.fullmatch(normalized)
        or any(part in {".", ".."} for part in normalized.split("/"))
    ):
        raise GitHubError("GITHUB_REPOSITORY must use the owner/repository format")
    return normalized


@dataclass(frozen=True)
class GitHubUrl:
    """A parsed ``https://github.com/owner/repo`` or ``.../pull/N`` URL."""

    owner: str
    repo: str
    number: int | None = None

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"


def _name_ok(part: str) -> bool:
    return part not in {".", ".."} and bool(part)


def parse_github_url(url: str) -> GitHubUrl | None:
    """Parse a GitHub repo or pull request URL. Other URLs return None.

    Accepts ``https://github.com/o/r``, ``https://github.com/o/r.git``, and
    ``https://github.com/o/r/pull/N`` (optional trailing slash).
    """
    text = url.strip()
    pull = _PR_URL_RE.fullmatch(text)
    if pull:
        owner, repo = pull.group("owner"), pull.group("repo")
        if _name_ok(owner) and _name_ok(repo):
            return GitHubUrl(owner=owner, repo=repo, number=int(pull.group("number")))
        return None
    repo_match = _REPO_URL_RE.fullmatch(text)
    if repo_match:
        owner, repo = repo_match.group("owner"), repo_match.group("repo")
        if repo.endswith(".git"):
            repo = repo[: -len(".git")]
        if _name_ok(owner) and _name_ok(repo):
            return GitHubUrl(owner=owner, repo=repo)
    return None


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _transport_error(exc: Exception, token: str | None) -> GitHubError:
    if isinstance(exc, httpx.HTTPStatusError):
        return GitHubError(f"GitHub API returned HTTP {exc.response.status_code}")
    return GitHubError(f"Could not reach the GitHub API: {redact_secrets(str(exc), token)}")


def _pull_from_record(
    pull: object,
    *,
    authors: set[str] | None,
    base_branch: str | None,
) -> GitHubPullRequest | None:
    """Parse one API record. ``None`` means the caller filtered it out."""
    if not isinstance(pull, dict):
        raise GitHubError("GitHub returned an invalid pull-request record")
    author = pull.get("user")
    head = pull.get("head")
    base = pull.get("base")
    if not isinstance(author, dict) or not isinstance(head, dict) or not isinstance(base, dict):
        raise GitHubError("GitHub returned an incomplete pull-request record")
    login = author.get("login")
    head_ref = head.get("ref")
    base_ref = base.get("ref")
    if not isinstance(login, str) or not isinstance(head_ref, str) or not isinstance(base_ref, str):
        raise GitHubError("GitHub returned invalid pull-request fields")
    if authors is not None and login.lower() not in authors:
        return None
    if base_branch is not None and base_ref != base_branch:
        return None
    try:
        return GitHubPullRequest(
            number=pull["number"],
            title=pull["title"],
            author=login,
            url=pull["html_url"],
            head_branch=head_ref,
            base_branch=base_ref,
            updated_at=pull["updated_at"],
        )
    except (KeyError, ValidationError) as exc:
        raise GitHubError("GitHub returned invalid pull-request fields") from exc


def _collect_pulls(
    repository: str,
    base_branch: str,
    token: str | None,
    *,
    authors: set[str] | None,
    limit: int | None = None,
) -> list[GitHubPullRequest]:
    pulls: list[GitHubPullRequest] = []
    page = 1
    try:
        with httpx.Client(timeout=15.0, headers=_headers(token)) as client:
            while True:
                response = client.get(
                    f"{_API_URL}/repos/{repository}/pulls",
                    params={
                        "state": "open",
                        "base": base_branch,
                        "per_page": 100,
                        "page": page,
                    },
                )
                response.raise_for_status()
                try:
                    page_pulls = response.json()
                except ValueError as exc:
                    raise GitHubError("GitHub returned invalid JSON") from exc
                if not isinstance(page_pulls, list):
                    raise GitHubError("GitHub returned an invalid pull-request response")
                for pull in page_pulls:
                    parsed = _pull_from_record(pull, authors=authors, base_branch=base_branch)
                    if parsed is None:
                        continue
                    pulls.append(parsed)
                    if limit is not None and len(pulls) >= limit:
                        return pulls
                if len(page_pulls) < 100:
                    break
                page += 1
    except GitHubError:
        raise
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _transport_error(exc, token) from exc
    return pulls


def list_open_pull_requests(
    repository: str,
    seated_users: set[str],
    base_branch: str,
    token: str | None = None,
) -> list[GitHubPullRequest]:
    repository = validate_repository(repository)
    if not seated_users:
        raise GitHubError("Configure at least one seated GitHub username")
    return _collect_pulls(repository, base_branch, token, authors=seated_users)


def list_open_pulls(
    repository: str,
    base_branch: str,
    token: str | None = None,
    *,
    limit: int | None = None,
) -> list[GitHubPullRequest]:
    """Open pull requests targeting ``base_branch``, any author.

    Unauthenticated unless ``token`` is set. The token is never included in
    the error text. ``limit`` stops once that many pulls have been kept.
    """
    repository = validate_repository(repository)
    return _collect_pulls(repository, base_branch, token, authors=None, limit=limit)


def get_pull_request(
    repository: str,
    number: int,
    token: str | None = None,
) -> GitHubPullRequest:
    """One pull request, open or closed. Unauthenticated unless ``token`` is set."""
    repository = validate_repository(repository)
    try:
        with httpx.Client(timeout=15.0, headers=_headers(token)) as client:
            response = client.get(f"{_API_URL}/repos/{repository}/pulls/{number}")
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise GitHubError("GitHub returned invalid JSON") from exc
    except GitHubError:
        raise
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _transport_error(exc, token) from exc
    parsed = _pull_from_record(payload, authors=None, base_branch=None)
    if parsed is None:
        raise GitHubError("GitHub returned an invalid pull-request record")
    return parsed
