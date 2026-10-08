"""HTTP routes for the Sentinel service."""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.schemas import AnalyzeRequest, AnalyzeResponse
from app.services import git_service, sentinel, verifier

router = APIRouter()


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
        return sentinel.analyze(
            repo,
            base,
            branches,
            use_cache=req.use_cache,
            verify=req.verify,
            # Generated tests only ever run on local repos under the allowed
            # roots (default: the bundled demo/), never on URL clones.
            verify_allowed=bool(req.repo_path) and verifier.repo_allowed(repo),
        )
    except git_service.GitError as e:
        raise HTTPException(400, str(e)) from e
