"""HTTP routes for Sentinel analysis and resolver proposals."""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.services import verifier

from app.config import settings
from app.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    GitHubPullRequestSyncResponse,
    GitHubReviewQueueResponse,
    MergeStepResponse,
    PlanRequest,
    PlanResponse,
    ResolveRequest,
    ResolveResponse,
)
from app.services import git_service, github_service, planner, resolver, sentinel

router = APIRouter()

# Directory names under verifier.DEMO_ROOT. The hosted image generates both.
_DEMO_REPO_NAMES = ("checkout-demo", "api-demo")


def bundled_demo_repos() -> list[Path]:
    """Absolute paths of the two repos the public demo may analyze."""
    root = verifier.DEMO_ROOT
    return [(root / name).resolve() for name in _DEMO_REPO_NAMES]


def _demo_mode_guard(req: AnalyzeRequest) -> None:
    """Refuse anything except the bundled demos before a path is opened or cloned."""
    if not settings.demo_mode:
        return
    allowed = bundled_demo_repos()
    detail = (
        "Demo mode only analyzes the bundled demo repositories "
        f"({', '.join(str(path) for path in allowed)}). "
        "repo_url and other paths are not allowed."
    )
    if req.repo_url or not (req.repo_path and req.repo_path.strip()):
        raise HTTPException(403, detail)
    try:
        repo = Path(req.repo_path).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(403, detail) from exc
    if repo not in allowed:
        raise HTTPException(403, detail)


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
    _demo_mode_guard(req)
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


@router.get("/github/queue", response_model=GitHubReviewQueueResponse)
def github_review_queue() -> GitHubReviewQueueResponse:
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
        items = github_service.list_review_queue(
            repository=repository,
            seated_users=settings.seated_github_users,
            base_branch=settings.default_base_branch,
            review_sla_hours=settings.github_review_sla_hours,
            token=settings.github_token or None,
        )
    except github_service.GitHubError as e:
        raise HTTPException(502, str(e)) from e

    return GitHubReviewQueueResponse(
        repository=repository,
        review_sla_hours=settings.github_review_sla_hours,
        items=items,
    )


@router.post("/plan", response_model=PlanResponse)
def plan(req: PlanRequest) -> PlanResponse:
    """Suggest a safe merge order for in-flight branches.

    Accepts either a pre-computed ``analysis`` object from a previous
    ``/api/analyze`` call, or a ``repo_path`` / ``repo_url`` so the Planner
    can run the Sentinel first.
    """
    if req.analysis is not None:
        analysis = req.analysis
        base = analysis.base_branch
    else:
        # Run Sentinel first, then plan.
        analyze_req = AnalyzeRequest(
            repo_path=req.repo_path,
            repo_url=req.repo_url,
            base_branch=req.base_branch,
            branches=req.branches,
        )
        try:
            analysis = analyze(analyze_req)  # reuse existing route logic
        except HTTPException:
            raise
        base = analysis.base_branch

    result = planner.plan(analysis)

    return PlanResponse(
        steps=[
            MergeStepResponse(
                branch=s.branch,
                position=s.position,
                zone=s.zone,
                risk_score=s.risk_score,
                status=s.status,
                conflicts_with=s.conflicts_with,
                rationale=s.rationale,
            )
            for s in result.steps
        ],
        zones=result.zones,
        summary=result.summary,
        parallelisable=result.parallelisable,
        base_branch=base,
        branches=analysis.branches,
    )