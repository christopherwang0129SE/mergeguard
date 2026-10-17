"""HTTP routes for Sentinel analysis and resolver proposals."""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    GitHubPullRequestSyncResponse,
    ResolveRequest,
    ResolveResponse,
)
from app.services import git_service, github_service, resolver, sentinel

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "nebius_ready": settings.nebius_ready,
        "tavily_ready": settings.tavily_ready,
    }


def _resolve_repo(req: AnalyzeRequest) -> Path:
    if req.repo_path:
        repo = Path(req.repo_path).expanduser().resolve()
        if not (repo / ".git").exists():
            raise HTTPException(400, f"{repo} is not a git repository")
        return repo
    if req.repo_url:
        slug = hashlib.sha1(req.repo_url.encode()).hexdigest()[:12]
        dest = Path(settings.workspace_dir).resolve() / slug
        try:
            return git_service.clone(req.repo_url, dest)
        except git_service.GitError as e:
            raise HTTPException(400, str(e)) from e
    raise HTTPException(400, "Provide repo_path or repo_url")


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    repo = _resolve_repo(req)
    base = req.base_branch or settings.default_base_branch

    try:
        branches = req.branches or git_service.list_branches(repo, exclude=base)
    except git_service.GitError as e:
        raise HTTPException(400, str(e)) from e
    if not branches:
        raise HTTPException(400, f"No branches to analyze (base={base}).")

    try:
        return sentinel.analyze(repo, base, branches)
    except git_service.GitError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/resolve", response_model=ResolveResponse)
def resolve(req: ResolveRequest) -> ResolveResponse:
    try:
        return resolver.resolve(req)
    except git_service.GitError as e:
        raise HTTPException(400, str(e)) from e


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
