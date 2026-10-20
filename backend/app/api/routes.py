"""HTTP routes for the Sentinel service."""
from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.services import verifier

from app.config import settings
from app.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    GitHubPullRequestSyncResponse,
    PairDiffRequest,
    PairDiffResponse,
    PairRisk,
)
from app.services import git_service, github_service, sentinel

router = APIRouter()

# Directory names under verifier.DEMO_ROOT. The hosted image generates both.
_DEMO_REPO_NAMES = ("checkout-demo", "api-demo")
# Other open PRs fetched alongside the one named in a pull URL.
_MAX_OTHER_PULLS = 8
# Silence longer than this on the stream yields a ping. Under 10s, so a long
# verify stage still produces a heartbeat at least every 10 seconds.
_HEARTBEAT_SECONDS = 8.0
_STREAM_END = object()
_PING = object()


class _RepoFields(Protocol):
    repo_path: str | None
    repo_url: str | None
    base_branch: str | None


@dataclass(frozen=True)
class OpenedRepo:
    path: Path
    base_branch: str
    # Set for a pull-request URL (the pr-N branches). None means "list them".
    branches: list[str] | None
    cloned: bool


def bundled_demo_repos() -> list[Path]:
    """Absolute paths of the two repos the public demo may analyze."""
    root = verifier.DEMO_ROOT
    return [(root / name).resolve() for name in _DEMO_REPO_NAMES]


def _demo_mode_guard_inputs(repo_path: str | None, repo_url: str | None) -> None:
    """Refuse anything except the bundled demos before a path is opened or cloned."""
    if not settings.demo_mode:
        return
    allowed = bundled_demo_repos()
    detail = (
        "Demo mode only analyzes the bundled demo repositories "
        f"({', '.join(str(path) for path in allowed)}). "
        "repo_url and other paths are not allowed."
    )
    if repo_url or not (repo_path and repo_path.strip()):
        raise HTTPException(403, detail)
    try:
        repo = Path(repo_path).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(403, detail) from exc
    if repo not in allowed:
        raise HTTPException(403, detail)


def _demo_mode_guard(req: AnalyzeRequest) -> None:
    _demo_mode_guard_inputs(req.repo_path, req.repo_url)


def _safe_detail(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    return git_service.redact_secrets(
        text,
        settings.github_token,
        settings.nebius_api_key,
        settings.tavily_api_key,
    )


def _token() -> str | None:
    token = settings.github_token.strip()
    return token or None


def _workspace_dest(key: str) -> Path:
    slug = hashlib.sha1(key.encode()).hexdigest()[:12]
    return Path(settings.workspace_dir).resolve() / slug


def _open_pull_request(parsed: github_service.GitHubUrl) -> OpenedRepo:
    """Clone the repo and fetch the PR head plus other open PRs on the same base."""
    token = _token()
    repository = parsed.repository
    number = parsed.number
    if number is None:
        raise HTTPException(400, "Pull request URL is missing a number")
    try:
        primary = github_service.get_pull_request(repository, number, token=token)
    except github_service.GitHubError as exc:
        raise HTTPException(400, _safe_detail(exc)) from exc
    try:
        others = github_service.list_open_pulls(
            repository,
            primary.base_branch,
            token=token,
            limit=_MAX_OTHER_PULLS + 1,
        )
    except github_service.GitHubError as exc:
        raise HTTPException(400, _safe_detail(exc)) from exc

    numbers = [primary.number]
    for pull in others:
        if pull.number in numbers:
            continue
        if len(numbers) >= 1 + _MAX_OTHER_PULLS:
            break
        numbers.append(pull.number)

    dest = _workspace_dest(parsed.clone_url)
    try:
        repo = git_service.clone(parsed.clone_url, dest, token=token)
    except git_service.GitError as exc:
        raise HTTPException(400, _safe_detail(exc)) from exc

    branches: list[str] = []
    for number in numbers:
        local = f"pr-{number}"
        try:
            git_service.fetch_pull_request(repo, number, token=token, local_branch=local)
        except git_service.GitError as exc:
            if number == primary.number:
                raise HTTPException(400, _safe_detail(exc)) from exc
            continue
        branches.append(local)
    if not branches:
        raise HTTPException(400, f"Could not fetch pull request {primary.number}")
    return OpenedRepo(
        path=repo,
        base_branch=git_service.prefer_ref(repo, primary.base_branch),
        branches=branches,
        cloned=True,
    )


def _open_request(req: _RepoFields) -> OpenedRepo:
    _demo_mode_guard_inputs(req.repo_path, req.repo_url)
    if req.repo_path:
        repo = Path(req.repo_path).expanduser().resolve()
        if not (repo / ".git").exists():
            raise HTTPException(400, f"{repo} is not a git repository")
        base = req.base_branch or settings.default_base_branch
        return OpenedRepo(path=repo, base_branch=base, branches=None, cloned=False)
    if req.repo_url and req.repo_url.strip():
        safe_url = git_service.strip_userinfo(req.repo_url.strip())
        parsed = github_service.parse_github_url(safe_url)
        if parsed is not None and parsed.number is not None:
            return _open_pull_request(parsed)
        token = _token()
        dest = _workspace_dest(safe_url)
        try:
            repo = git_service.clone(safe_url, dest, token=token)
        except git_service.GitError as exc:
            raise HTTPException(400, _safe_detail(exc)) from exc
        requested = req.base_branch or settings.default_base_branch
        return OpenedRepo(
            path=repo,
            base_branch=git_service.prefer_ref(repo, requested),
            branches=None,
            cloned=True,
        )
    raise HTTPException(400, "Provide repo_path or repo_url")


def _branches_to_analyze(opened: OpenedRepo, requested: list[str] | None) -> list[str]:
    if requested:
        return requested
    if opened.branches is not None:
        return list(opened.branches)
    if opened.cloned:
        return git_service.list_remote_branches(opened.path, exclude=opened.base_branch)
    return git_service.list_branches(opened.path, exclude=opened.base_branch)


def _prepare(req: AnalyzeRequest) -> tuple[OpenedRepo, list[str]]:
    """Resolve the repo and the branch list. Failures are HTTP errors, not stream events."""
    opened = _open_request(req)
    if not git_service.ref_exists(opened.path, opened.base_branch):
        raise HTTPException(400, f"Base branch {opened.base_branch!r} was not found")
    try:
        branches = _branches_to_analyze(opened, req.branches)
    except git_service.GitError as exc:
        raise HTTPException(400, _safe_detail(exc)) from exc
    if not branches:
        raise HTTPException(400, f"No branches to analyze (base={opened.base_branch}).")
    return opened, branches


def _verify_allowed(req: AnalyzeRequest, repo: Path) -> bool:
    # Generated tests only ever run on local repos under the allowed
    # roots (default: the bundled demo/), never on URL clones.
    return bool(req.repo_path) and verifier.repo_allowed(repo)


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "nebius_ready": settings.nebius_ready,
        "nebius_enabled": settings.nebius_enabled,
        "nebius_model": settings.nebius_model,
        "model": settings.nebius_model,
        "provider": settings.nebius_provider,
        "base_url": settings.nebius_base_url,
        "tavily_ready": settings.tavily_ready,
    }


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    opened, branches = _prepare(req)
    try:
        return sentinel.analyze(
            opened.path,
            opened.base_branch,
            branches,
            use_cache=req.use_cache,
            verify=req.verify,
            verify_allowed=_verify_allowed(req, opened.path),
        )
    except git_service.GitError as exc:
        raise HTTPException(400, _safe_detail(exc)) from exc


def _stamp(started: float, event: dict) -> dict:
    stamped = dict(event)
    pair = stamped.get("pair")
    if isinstance(pair, PairRisk):
        stamped["pair"] = pair.model_dump(mode="json")
    result = stamped.get("result")
    if isinstance(result, AnalyzeResponse):
        stamped["result"] = result.model_dump(mode="json")
    stamped["t"] = round(time.monotonic() - started, 2)
    return stamped


def _pull_event(events: queue.Queue):
    try:
        return events.get(timeout=_HEARTBEAT_SECONDS)
    except queue.Empty:
        return _PING


async def _iterate_ndjson(events: queue.Queue, started: float):
    while True:
        item = await asyncio.to_thread(_pull_event, events)
        if item is _STREAM_END:
            break
        if item is _PING:
            payload = {"type": "ping", "t": round(time.monotonic() - started, 2)}
        else:
            payload = item
        yield json.dumps(payload) + "\n"


@router.post("/analyze/stream")
def analyze_stream(req: AnalyzeRequest) -> StreamingResponse:
    """Same body as ``/api/analyze``. NDJSON events, then one final result.

    Demo-mode, path, clone, and empty-branch failures are ordinary HTTP
    errors and produce no stream. Anything that fails after the stream has
    started is a final ``error`` line.
    """
    opened, branches = _prepare(req)
    events: queue.Queue = queue.Queue()
    started = time.monotonic()

    def progress(event: dict) -> None:
        events.put(_stamp(started, event))

    def worker() -> None:
        try:
            progress(
                {
                    "type": "stage",
                    "stage": "resolve",
                    "status": "start",
                    "label": "Opening the repository",
                }
            )
            progress(
                {
                    "type": "stage",
                    "stage": "resolve",
                    "status": "done",
                    "label": "Repository ready",
                    "detail": opened.path.name,
                }
            )
            progress(
                {
                    "type": "branches",
                    "base_branch": opened.base_branch,
                    "branches": branches,
                }
            )
            result = sentinel.analyze(
                opened.path,
                opened.base_branch,
                branches,
                use_cache=req.use_cache,
                verify=req.verify,
                verify_allowed=_verify_allowed(req, opened.path),
                progress=progress,
            )
            progress({"type": "result", "result": result})
        except Exception as exc:  # noqa: BLE001 - the stream has to end with an error line
            events.put(
                _stamp(started, {"type": "error", "detail": _safe_detail(exc)})
            )
        finally:
            events.put(_STREAM_END)

    threading.Thread(target=worker, name="analyze-stream", daemon=True).start()
    return StreamingResponse(
        _iterate_ndjson(events, started),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/pair-diff", response_model=PairDiffResponse)
def pair_diff(req: PairDiffRequest) -> PairDiffResponse:
    """Both branches' diffs against the base. Same repo rules as ``/api/analyze``."""
    opened = _open_request(req)
    if not git_service.ref_exists(opened.path, opened.base_branch):
        raise HTTPException(400, f"Base branch {opened.base_branch!r} was not found")
    try:
        return git_service.pair_diff(
            opened.path,
            opened.base_branch,
            req.branch_a,
            req.branch_b,
            context=req.context,
        )
    except git_service.GitError as exc:
        raise HTTPException(400, _safe_detail(exc)) from exc


@router.get("/github/pull-requests", response_model=GitHubPullRequestSyncResponse)
def sync_github_pull_requests() -> GitHubPullRequestSyncResponse:
    repository = settings.github_repository.strip()
    if not repository:
        raise HTTPException(503, "GITHUB_REPOSITORY is not configured")
    if not settings.seated_github_users:
        raise HTTPException(503, "GITHUB_SEATED_USERS is not configured")
    try:
        repository = github_service.validate_repository(repository)
    except github_service.GitHubError as e:
        raise HTTPException(503, str(e)) from e
    try:
        pull_requests = github_service.list_open_pull_requests(
            repository=repository,
            seated_users=settings.seated_github_users,
            base_branch=settings.default_base_branch,
            token=settings.github_token or None,
        )
    except github_service.GitHubError as e:
        raise HTTPException(502, str(e)) from e

    owner, name = repository.split("/", 1)
    clone_url = f"https://github.com/{owner}/{name}.git"
    slug = hashlib.sha1(repository.lower().encode()).hexdigest()[:12]
    dest = Path(settings.workspace_dir).resolve() / slug
    try:
        repo = git_service.clone(clone_url, dest, token=settings.github_token or None)
        branches = [
            git_service.fetch_pull_request(repo, pull.number, token=settings.github_token or None)
            for pull in pull_requests
        ]
        analysis = sentinel.analyze(
            repo,
            f"origin/{settings.default_base_branch}",
            branches,
        )
    except git_service.GitError as e:
        raise HTTPException(502, str(e)) from e

    return GitHubPullRequestSyncResponse(
        repository=repository,
        pull_requests=pull_requests,
        analysis=analysis,
    )
