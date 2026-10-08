"""Sentinel — predicts merge-conflict risk between in-flight branches.

Given a repo and a set of branches, each branch is diffed against the merge-base
with the integration branch. For every branch *pair* we look at the files both
touch, and within those files whether their changed line-ranges overlap or sit
close together. That yields a 0-100 git risk score with an explainable rationale.

When Nebius is configured (``NEBIUS_API_KEY`` set and ``NEBIUS_ENABLED``), the
highest-scoring pairs — up to ``NEBIUS_MAX_PAIRS`` — are also sent to NVIDIA
Nemotron on Token Factory. The published score is an equal blend of the git
score and the model's ``risk_score``. If the hunks overlap, the blend cannot
fall below the git score: the model may raise a textual collision, not dismiss
it. When they do not overlap, the blend can move either way so a semantic clash
the diff heuristic missed can still raise the score.

If the key is missing, the model is disabled, the call errors or times out, or
the reply is not a usable JSON assessment, that pair keeps the git score and
``fallback_reason`` says why. A model failure never fails the request.
"""
from __future__ import annotations

from itertools import combinations
from pathlib import Path

from app.config import settings
from app.schemas import (
    AnalyzeResponse,
    BranchDiff,
    ChangedFile,
    FallbackReason,
    FileCollision,
    PairRisk,
    RiskLevel,
)
from app.services import git_service
from app.services.nemotron import (
    NemotronError,
    assess_pair,
    build_pair_prompt,
)

# Two changed regions within this many lines of each other are treated as
# colliding — git conflicts arise from adjacency, not just exact overlap.
ADJACENCY_LINES = 3


def _ranges_collide(a: tuple[int, int], b: tuple[int, int], pad: int = ADJACENCY_LINES) -> bool:
    return a[0] - pad <= b[1] and b[0] - pad <= a[1]


def _hunks_collide(
    a_hunks: list[tuple[int, int]], b_hunks: list[tuple[int, int]]
) -> bool:
    return any(_ranges_collide(a, b) for a in a_hunks for b in b_hunks)


def _file_collision(a: ChangedFile, b: ChangedFile) -> FileCollision:
    return FileCollision(
        path=a.path,
        overlapping=_hunks_collide(a.hunks, b.hunks),
        branch_a_hunks=a.hunks,
        branch_b_hunks=b.hunks,
    )


def _level(score: float) -> RiskLevel:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def combine_scores(git_score: float, model_score: float, overlapping_files: int) -> float:
    """Blend the git hunk score with the model score. See the module docstring."""
    blended = (0.5 * git_score) + (0.5 * model_score)
    if overlapping_files > 0:
        blended = max(git_score, blended)
    blended = min(max(blended, 0.0), 100.0)
    return round(blended, 1)


def _score_pair(diff_a: BranchDiff, diff_b: BranchDiff) -> PairRisk:
    files_a = {f.path: f for f in diff_a.files}
    files_b = {f.path: f for f in diff_b.files}
    shared = sorted(set(files_a) & set(files_b))

    collisions: list[FileCollision] = []
    overlapping = 0
    for path in shared:
        col = _file_collision(files_a[path], files_b[path])
        collisions.append(col)
        if col.overlapping:
            overlapping += 1

    # Scoring: touching the same file is a warning; overlapping regions are the
    # real risk. Overlaps dominate; shared-but-disjoint files add a smaller base.
    score = 0.0
    if shared:
        score += 25.0 * (1 - 0.6 ** len(shared))  # up to ~25 from shared files
    if overlapping:
        score += 75.0 * (1 - 0.5 ** overlapping)  # up to ~75 from real overlaps
    score = round(min(score, 100.0), 1)

    if overlapping:
        rationale = (
            f"{overlapping} file(s) changed in overlapping regions by both branches — "
            f"a textual merge conflict is likely."
        )
    elif shared:
        rationale = (
            f"{len(shared)} shared file(s) but no overlapping regions — "
            f"merges cleanly today, but coordinate before divergence grows."
        )
    else:
        rationale = "No shared files — these branches are independent."

    return PairRisk(
        branch_a=diff_a.branch,
        branch_b=diff_b.branch,
        score=score,
        level=_level(score),
        shared_files=len(shared),
        overlapping_files=overlapping,
        collisions=[c for c in collisions if c.overlapping] or collisions,
        rationale=rationale,
        score_source="git",
        git_score=score,
        git_rationale=rationale,
    )


def _model_skip_reason() -> FallbackReason | None:
    if not settings.nebius_enabled:
        return "disabled"
    if not settings.nebius_api_key.strip():
        return "no_api_key"
    return None


def _commits(repo: Path, branch: str) -> list[str]:
    try:
        return git_service.recent_commit_subjects(repo, branch, limit=5)
    except git_service.GitError:
        return []


def _excerpt(repo: Path, base_branch: str, pair: PairRisk) -> str:
    paths = [c.path for c in pair.collisions if c.overlapping] or [
        c.path for c in pair.collisions
    ]
    paths = paths[:3]
    if not paths:
        return ""
    chunks: list[str] = []
    for branch in (pair.branch_a, pair.branch_b):
        try:
            text = git_service.trimmed_diff(
                repo, branch, base_branch, paths, max_chars=1100
            )
        except git_service.GitError:
            text = ""
        if text.strip():
            chunks.append(f"--- {branch} ---\n{text.strip()}")
    return "\n".join(chunks)


def _refine_one(
    repo: Path,
    base_branch: str,
    diff_by_branch: dict[str, BranchDiff],
    pair: PairRisk,
) -> PairRisk:
    diff_a = diff_by_branch[pair.branch_a]
    diff_b = diff_by_branch[pair.branch_b]
    try:
        prompt = build_pair_prompt(
            base_branch=base_branch,
            pair=pair,
            diff_a=diff_a,
            diff_b=diff_b,
            commits_a=_commits(repo, pair.branch_a),
            commits_b=_commits(repo, pair.branch_b),
            excerpt=_excerpt(repo, base_branch, pair),
        )
        assessment = assess_pair(prompt)
    except NemotronError as exc:
        reason: FallbackReason = "unparsable" if exc.code == "unparsable" else "model_error"
        return pair.model_copy(update={"fallback_reason": reason})
    except Exception:
        return pair.model_copy(update={"fallback_reason": "model_error"})

    git_score = pair.git_score if pair.git_score is not None else pair.score
    published = combine_scores(git_score, assessment.risk_score, pair.overlapping_files)
    model_level = assessment.risk_level or _level(assessment.risk_score)
    return pair.model_copy(
        update={
            "score": published,
            "level": _level(published),
            "rationale": assessment.rationale,
            "score_source": "model",
            "model_score": assessment.risk_score,
            "model_level": model_level,
            "model_rationale": assessment.rationale,
            "fallback_reason": None,
        }
    )


def _refine_with_model(
    repo: Path,
    base_branch: str,
    diff_by_branch: dict[str, BranchDiff],
    pairs: list[PairRisk],
) -> list[PairRisk]:
    """Call the model for the top ``nebius_max_pairs`` pairs by git score."""
    limit = max(0, int(settings.nebius_max_pairs))
    refined: list[PairRisk] = []
    for index, pair in enumerate(pairs):
        if index >= limit:
            refined.append(pair.model_copy(update={"fallback_reason": "pair_cap"}))
            continue
        refined.append(_refine_one(repo, base_branch, diff_by_branch, pair))
    return refined


def analyze(
    repo: Path,
    base_branch: str,
    branches: list[str],
) -> AnalyzeResponse:
    diffs = [git_service.diff_against_base(repo, b, base_branch) for b in branches]
    diff_by_branch = {d.branch: d for d in diffs}

    pairs: list[PairRisk] = []
    for a, b in combinations(branches, 2):
        pairs.append(_score_pair(diff_by_branch[a], diff_by_branch[b]))
    pairs.sort(key=lambda p: p.score, reverse=True)

    skip = _model_skip_reason()
    if skip:
        pairs = [pair.model_copy(update={"fallback_reason": skip}) for pair in pairs]
    else:
        pairs = _refine_with_model(repo, base_branch, diff_by_branch, pairs)
        pairs.sort(key=lambda p: p.score, reverse=True)

    risk_model = settings.nebius_model if any(p.score_source == "model" for p in pairs) else None
    return AnalyzeResponse(
        base_branch=base_branch,
        branches=branches,
        diffs=diffs,
        pairs=pairs,
        risk_model=risk_model,
    )
