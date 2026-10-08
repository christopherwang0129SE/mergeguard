"""Sentinel — predicts merge-conflict risk between in-flight branches.

Given a repo and a set of branches, each branch is diffed against the merge-base
with the integration branch. For every branch *pair* we look at the files both
touch, and within those files whether their changed line-ranges overlap or sit
close together. That yields a 0-100 git risk score with an explainable rationale.

Cost-aware pipeline. Before any LLM call a cheap static prefilter
(``app/services/prefilter.py``) keeps only pairs that share a file or whose
changed symbols overlap (A edits ``def f``, B calls ``f``). Harmless pairs are
marked ``fallback_reason="prefiltered"`` and never reach the model. The top K
candidates (``NEBIUS_MAX_PAIRS``, default 5) are sent to NVIDIA Nemotron on
Token Factory; results are cached on disk keyed by (base sha, head shas,
model, prompt version), so an identical re-run makes zero LLM calls. Token
usage and estimated $ are returned in ``usage``.

When Nebius is configured (``NEBIUS_API_KEY`` set and ``NEBIUS_ENABLED``), the
selected pairs are sent to NVIDIA Nemotron. The published score is an equal blend of the git
score and the model's ``risk_score``. If the hunks overlap, the blend cannot
fall below the git score: the model may raise a textual collision, not dismiss
it. When they do not overlap, the blend can move either way so a semantic clash
the diff heuristic missed can still raise the score.

If the key is missing, the model is disabled, the call errors or times out, or
the reply is not a usable JSON assessment, that pair keeps the git score and
``fallback_reason`` says why. A model failure never fails the request.
"""
from __future__ import annotations

import time
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
    UsageSummary,
    Verification,
)
from app.services import cache, git_service, nemotron, prefilter, verifier
from app.services.nemotron import (
    NemotronError,
    build_pair_prompt,
    parse_assessment,
)
from app.services.usage import UsageMeter

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
                repo,
                branch,
                base_branch,
                paths,
                max_chars=2000,
                function_context=True,
            )
        except git_service.GitError:
            text = ""
        if text.strip():
            chunks.append(f"--- {branch} ---\n{text.strip()}")
    return "\n".join(chunks)


def _apply_assessment(
    pair: PairRisk,
    *,
    risk_score: float,
    rationale: str,
    risk_level: RiskLevel | None,
    cached: bool,
    prompt_tokens: int,
    completion_tokens: int,
) -> PairRisk:
    git_score = pair.git_score if pair.git_score is not None else pair.score
    published = combine_scores(git_score, risk_score, pair.overlapping_files)
    model_level = risk_level or _level(risk_score)
    return pair.model_copy(
        update={
            "score": published,
            "level": _level(published),
            "rationale": rationale,
            "score_source": "model",
            "model_score": risk_score,
            "model_level": model_level,
            "model_rationale": rationale,
            "fallback_reason": None,
            "model_cached": cached,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
    )


def _sha(repo: Path, ref: str) -> str:
    try:
        return git_service.rev_parse(repo, ref)
    except git_service.GitError:
        return ref


def _refine_one(
    repo: Path,
    base_branch: str,
    diff_by_branch: dict[str, BranchDiff],
    pair: PairRisk,
    *,
    meter: UsageMeter | None = None,
    use_cache: bool = True,
) -> PairRisk:
    meter = meter or UsageMeter()
    key = cache.cache_key(
        base_sha=_sha(repo, base_branch),
        head_a=_sha(repo, pair.branch_a),
        head_b=_sha(repo, pair.branch_b),
        model=settings.nebius_model,
    )
    if use_cache:
        hit = cache.get(key)
        if hit is not None:
            meter.record_cache_hit()
            level = hit.risk_level if hit.risk_level in ("none", "low", "medium", "high") else None
            return _apply_assessment(
                pair,
                risk_score=hit.risk_score,
                rationale=hit.rationale,
                risk_level=level,  # type: ignore[arg-type]
                cached=True,
                prompt_tokens=0,
                completion_tokens=0,
            )

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
        reply = nemotron.complete(prompt)
        prompt_tokens, completion_tokens = nemotron.usage_of(reply)
        meter.record_call(prompt_tokens, completion_tokens)
        assessment = parse_assessment(reply)
    except NemotronError as exc:
        if exc.prompt_tokens or exc.completion_tokens:
            meter.record_call(exc.prompt_tokens, exc.completion_tokens)
        reason: FallbackReason = "unparsable" if exc.code == "unparsable" else "model_error"
        return pair.model_copy(update={"fallback_reason": reason})
    except Exception:
        return pair.model_copy(update={"fallback_reason": "model_error"})

    cache.put(
        key,
        cache.from_payload(
            {
                "risk_score": assessment.risk_score,
                "rationale": assessment.rationale,
                "risk_level": assessment.risk_level,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "estimated_cost_usd": nemotron.estimate_cost_usd(prompt_tokens, completion_tokens),
            },
            model=settings.nebius_model,
        ),
    )
    return _apply_assessment(
        pair,
        risk_score=assessment.risk_score,
        rationale=assessment.rationale,
        risk_level=assessment.risk_level,
        cached=False,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _prefilter_signals(
    repo: Path,
    diff_by_branch: dict[str, BranchDiff],
    pairs: list[PairRisk],
) -> dict[tuple[str, str], prefilter.PairSignal]:
    symbols = {
        branch: prefilter.branch_symbols(repo, diff) for branch, diff in diff_by_branch.items()
    }
    signals: dict[tuple[str, str], prefilter.PairSignal] = {}
    for pair in pairs:
        signals[(pair.branch_a, pair.branch_b)] = prefilter.PairSignal(
            shared_files=pair.shared_files,
            overlapping_files=pair.overlapping_files,
            symbols=prefilter.symbol_overlap(symbols[pair.branch_a], symbols[pair.branch_b]),
        )
    return signals


def _refine_with_model(
    repo: Path,
    base_branch: str,
    diff_by_branch: dict[str, BranchDiff],
    pairs: list[PairRisk],
    *,
    meter: UsageMeter | None = None,
    use_cache: bool = True,
) -> list[PairRisk]:
    """Send only prefilter candidates, top K by signal, to the model."""
    limit = max(0, int(settings.nebius_max_pairs))
    candidates = [p for p in pairs if p.prefilter_candidate]
    candidates.sort(
        key=lambda p: (p.overlapping_files, len(p.symbol_overlap), p.shared_files, p.score),
        reverse=True,
    )
    selected = {(p.branch_a, p.branch_b) for p in candidates[:limit]}
    refined: list[PairRisk] = []
    for pair in pairs:
        ident = (pair.branch_a, pair.branch_b)
        if not pair.prefilter_candidate:
            refined.append(pair.model_copy(update={"fallback_reason": "prefiltered"}))
        elif ident not in selected:
            refined.append(pair.model_copy(update={"fallback_reason": "pair_cap"}))
        else:
            refined.append(
                _refine_one(
                    repo, base_branch, diff_by_branch, pair, meter=meter, use_cache=use_cache
                )
            )
    return refined


def _verify_high_risk(
    repo: Path,
    base_branch: str,
    pairs: list[PairRisk],
    *,
    meter: UsageMeter,
    use_cache: bool,
    allowed: bool,
) -> list[PairRisk]:
    limit = max(0, int(settings.verify_max_pairs))
    chosen = [p for p in pairs if verifier.is_high_risk(p)][:limit]
    chosen_ids = {(p.branch_a, p.branch_b) for p in chosen}
    out: list[PairRisk] = []
    for pair in pairs:
        if (pair.branch_a, pair.branch_b) not in chosen_ids:
            out.append(pair)
            continue
        if not allowed:
            result = Verification(
                status="skipped",
                reason="verify-by-test only runs on local repos under the allowed roots",
            )
        else:
            result = verifier.verify_pair(
                repo, base_branch, pair, meter=meter, use_cache=use_cache
            )
        out.append(pair.model_copy(update={"verification": result}))
    return out


def analyze(
    repo: Path,
    base_branch: str,
    branches: list[str],
    *,
    use_cache: bool = True,
    verify: bool = False,
    verify_allowed: bool = False,
) -> AnalyzeResponse:
    """Score every branch pair.

    ``verify`` runs verify-by-test on high-risk model-scored pairs.
    ``verify_allowed`` must be True for any generated test to execute: the
    caller decides it (local repo under the allowed roots, never a URL clone).
    """
    started = time.monotonic()
    meter = UsageMeter()
    diffs = [git_service.diff_against_base(repo, b, base_branch) for b in branches]
    diff_by_branch = {d.branch: d for d in diffs}

    pairs: list[PairRisk] = []
    for a, b in combinations(branches, 2):
        pairs.append(_score_pair(diff_by_branch[a], diff_by_branch[b]))
    pairs.sort(key=lambda p: p.score, reverse=True)

    signals = _prefilter_signals(repo, diff_by_branch, pairs)
    pairs = [
        p.model_copy(
            update={
                "symbol_overlap": list(signals[(p.branch_a, p.branch_b)].symbols),
                "prefilter_candidate": signals[(p.branch_a, p.branch_b)].is_candidate,
            }
        )
        for p in pairs
    ]

    skip = _model_skip_reason()
    if skip:
        pairs = [pair.model_copy(update={"fallback_reason": skip}) for pair in pairs]
    else:
        pairs = _refine_with_model(
            repo, base_branch, diff_by_branch, pairs, meter=meter, use_cache=use_cache
        )
        pairs.sort(key=lambda p: p.score, reverse=True)

    if verify and settings.verify_enabled and not skip:
        pairs = _verify_high_risk(
            repo, base_branch, pairs, meter=meter, use_cache=use_cache, allowed=verify_allowed
        )

    risk_model = settings.nebius_model if any(p.score_source == "model" for p in pairs) else None
    usage = UsageSummary(
        **meter.summary_fields(),
        pairs_total=len(pairs),
        pairs_prefiltered=sum(1 for p in pairs if not p.prefilter_candidate),
        pairs_sent_to_model=sum(1 for p in pairs if p.score_source == "model"),
        pairs_capped=sum(1 for p in pairs if p.fallback_reason == "pair_cap"),
        top_k=max(0, int(settings.nebius_max_pairs)),
        verify_pairs=sum(
            1 for p in pairs if p.verification and p.verification.status != "skipped"
        ),
        verify_confirmed=sum(
            1 for p in pairs if p.verification and p.verification.status == "confirmed"
        ),
        elapsed_s=round(time.monotonic() - started, 2),
    )
    return AnalyzeResponse(
        base_branch=base_branch,
        branches=branches,
        diffs=diffs,
        pairs=pairs,
        risk_model=risk_model,
        usage=usage,
    )
