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
model, prompt version), so an identical re-run makes zero LLM calls. The
cached entry includes that pair's ``docs_sources`` and a hit restores them
with no new Tavily lookup. Token usage and estimated $ are returned in
``usage``.

When Tavily is configured (``TAVILY_API_KEY`` set and ``TAVILY_ENABLED``),
third-party libraries imported by a pair are looked up and a short excerpt is
added to the Nemotron prompts. A missing key, ``TAVILY_ENABLED=false``, or a
search error leaves those prompts unchanged.

When Nebius is configured (``NEBIUS_API_KEY`` set and ``NEBIUS_ENABLED``), the
selected pairs are sent to NVIDIA Nemotron. The published score is an equal blend of the git
score and the model's ``risk_score``. If the hunks overlap, the blend cannot
fall below the git score unless ``git merge-tree`` merged cleanly
(``textual_conflict`` is false): the model may raise a real textual collision,
not dismiss it. A clean merge uses the plain 0.5/0.5 blend. When hunks do not
overlap, the blend can move either way so a semantic clash the diff heuristic
missed can still raise the score. The floor also stays when merge-tree errors
(``textual_conflict`` is unknown).

A clean merge is published as high only when the model itself said high
(``score_source == "model"`` and ``model_level == "high"``). Otherwise the
score is capped at 69, so the level is at most medium, and ``score_note``
says why when that cap changed the score. The cap runs after verify-by-test.
``is_high_risk`` reads the level and the score, and ``verify_min_score`` is
70, so capping first would drop pairs whose blend cleared 70 only because
the git score lifted a lower model assessment. A verification that already
confirmed is left alone. Branch tests run after the cap and still raise a
confirmed pair.

If the key is missing, the model is disabled, the call errors or times out, or
the reply is not a usable JSON assessment, that pair keeps the git score and
``fallback_reason`` says why. A model failure never fails the request.
"""
from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from itertools import combinations
from pathlib import Path
from typing import Literal

from app.config import settings
from app.schemas import (
    AnalyzeResponse,
    BranchDiff,
    BranchTests,
    ChangedFile,
    DocSource,
    FallbackReason,
    FileCollision,
    PairRisk,
    RiskLevel,
    UsageSummary,
    Verification,
)
from app.services import (
    branch_tests,
    cache,
    claims,
    docs_lookup,
    git_service,
    nemotron,
    prefilter,
    verifier,
)
from app.services.config_paths import is_config_path as _is_config_path
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


def combine_scores(
    git_score: float,
    model_score: float,
    overlapping_files: int,
    *,
    textual_conflict: bool | None = None,
) -> float:
    """Blend the git hunk score with the model score.

    Overlapping hunks floor the blend at ``git_score`` unless git merged
    cleanly (``textual_conflict is False``). Unknown (``None``) keeps the floor.
    """
    blended = (0.5 * git_score) + (0.5 * model_score)
    if overlapping_files > 0 and textual_conflict is not False:
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


_DOC_SUFFIXES = frozenset({".md", ".rst", ".txt"})
# A path with none of these suffixes is non-code, so it counts as test data
# unless it is config or docs. ``tests/**/data/`` is test data even when the
# file itself is code.
_CODE_SUFFIXES = frozenset({
    ".py", ".pyi", ".pyx",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts",
    ".go", ".rs", ".java", ".kt", ".kts", ".scala",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp",
    ".cs", ".swift", ".m", ".mm",
    ".rb", ".php", ".lua", ".pl", ".pm",
    ".ex", ".exs", ".erl", ".hs", ".ml", ".mli",
    ".clj", ".cljs", ".dart",
    ".vue", ".svelte",
    ".sh", ".bash", ".zsh", ".ps1",
    ".sql",
    ".css", ".scss", ".sass", ".less",
    ".html", ".htm",
    ".graphql", ".gql", ".proto",
})
_EXCERPT_PATH_LIMIT = 3
_PathKind = Literal["config", "docs", "test_data", "source"]


def _path_parts(path: str) -> list[str]:
    return [part for part in path.replace("\\", "/").split("/") if part not in {"", "."}]


def _basename(path: str) -> str:
    parts = _path_parts(path)
    return parts[-1] if parts else ""


def _is_docs_path(path: str) -> bool:
    parts = _path_parts(path)
    if not parts:
        return False
    if Path(parts[-1]).suffix.lower() in _DOC_SUFFIXES:
        return True
    return any(part.lower() == "docs" for part in parts[:-1])


def _is_under_tests_data(path: str) -> bool:
    parts = [part.lower() for part in _path_parts(path)]
    for index, part in enumerate(parts[:-1]):
        if part == "tests" and "data" in parts[index + 1 : -1]:
            return True
    return False


def _path_kind(path: str) -> _PathKind:
    """Source is everything that is not config, docs/changelog, or test data."""
    if _is_config_path(path):
        return "config"
    if _is_docs_path(path):
        return "docs"
    suffix = Path(_basename(path)).suffix.lower()
    if _is_under_tests_data(path) or suffix not in _CODE_SUFFIXES:
        return "test_data"
    return "source"


def _unique_paths(files: Iterable[ChangedFile | str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in files:
        path = item if isinstance(item, str) else item.path
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def _extend_unique(selected: list[str], seen: set[str], paths: Iterable[str]) -> None:
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        selected.append(path)
        if len(selected) >= _EXCERPT_PATH_LIMIT:
            return


def _shared_of(shared: Sequence[str], overlapping: set[str], kind: _PathKind) -> list[str]:
    chosen = [path for path in shared if _path_kind(path) == kind]
    return [path for path in chosen if path in overlapping] + [
        path for path in chosen if path not in overlapping
    ]


def _excerpt_paths(
    pair: PairRisk,
    files_a: Iterable[ChangedFile | str],
    files_b: Iterable[ChangedFile | str],
) -> list[str]:
    """Up to three diff paths, highest priority first.

    Shared source (overlapping hunks first), then prefilter focus paths, then
    config either branch changed. Docs, changelogs and test data are included
    only when none of those exist.
    """
    paths_a = _unique_paths(files_a)
    paths_b = _unique_paths(files_b)
    in_b = set(paths_b)
    shared = [path for path in paths_a if path in in_b]
    overlapping = {col.path for col in pair.collisions if col.overlapping}

    selected: list[str] = []
    seen: set[str] = set()
    _extend_unique(selected, seen, _shared_of(shared, overlapping, "source"))
    if len(selected) < _EXCERPT_PATH_LIMIT:
        _extend_unique(selected, seen, verifier.focus_paths(pair.symbol_evidence))
    if len(selected) < _EXCERPT_PATH_LIMIT:
        either = _unique_paths([*paths_a, *paths_b])
        _extend_unique(
            selected, seen, [path for path in either if _path_kind(path) == "config"]
        )
    if not selected:
        fallback = _shared_of(shared, overlapping, "docs") + _shared_of(
            shared, overlapping, "test_data"
        )
        _extend_unique(selected, seen, fallback)
        if len(selected) < _EXCERPT_PATH_LIMIT:
            either = _unique_paths([*paths_a, *paths_b])
            _extend_unique(
                selected,
                seen,
                [path for path in either if _path_kind(path) in {"docs", "test_data"}],
            )
    return selected


def _excerpt(
    repo: Path,
    base_branch: str,
    pair: PairRisk,
    files_a: Iterable[ChangedFile | str],
    files_b: Iterable[ChangedFile | str],
) -> str:
    paths = _excerpt_paths(pair, files_a, files_b)
    chunks: list[str] = []
    try:
        snippets = prefilter.evidence_snippets(repo, base_branch, pair.symbol_evidence)
    except git_service.GitError:
        snippets = ""
    if snippets:
        chunks.append(f"--- prefilter evidence: changed definition and call site ---\n{snippets}")
    if not paths:
        return "\n".join(chunks)
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
    published = combine_scores(
        git_score,
        risk_score,
        pair.overlapping_files,
        textual_conflict=pair.textual_conflict,
    )
    unsupported = claims.unsupported_claims(rationale, pair.static_compatible)
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
            "unsupported_claims": unsupported,
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
            # Stored with the assessment. A hit must not call Tavily or the model.
            # Entries written before the field existed load as an empty list.
            pair = pair.model_copy(
                update={"docs_sources": [DocSource(**item) for item in hit.docs_sources]}
            )
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
    snippets = docs_lookup.lookup_for_pair(
        repo, base_branch, pair.branch_a, pair.branch_b, diff_by_branch=diff_by_branch,
    )
    if snippets:
        # model_copy does not re-validate, so build the models here.
        pair = pair.model_copy(update={
            "docs_sources": [DocSource(**item) for item in docs_lookup.to_sources(snippets)],
        })
    try:
        prompt = build_pair_prompt(
            base_branch=base_branch,
            pair=pair,
            diff_a=diff_a,
            diff_b=diff_b,
            commits_a=_commits(repo, pair.branch_a),
            commits_b=_commits(repo, pair.branch_b),
            excerpt=_excerpt(repo, base_branch, pair, diff_a.files, diff_b.files),
            docs=docs_lookup.format_docs_section(snippets),
        )
        reply = nemotron.complete(prompt)
        prompt_tokens, completion_tokens = nemotron.usage_of(reply)
        meter.record_call(prompt_tokens, completion_tokens)
        assessment = parse_assessment(reply)
        contradicted = claims.unsupported_claims(assessment.rationale, pair.static_compatible)
        if contradicted:
            # One repair round: show the model the exact static facts it contradicted.
            try:
                retry = nemotron.complete(claims.repair_prompt(prompt, reply, contradicted))
                p_tok, c_tok = nemotron.usage_of(retry)
                meter.record_call(p_tok, c_tok)
                prompt_tokens += p_tok
                completion_tokens += c_tok
                assessment = parse_assessment(retry)
            except NemotronError as exc:
                if exc.prompt_tokens or exc.completion_tokens:
                    meter.record_call(exc.prompt_tokens, exc.completion_tokens)
                    prompt_tokens += exc.prompt_tokens
                    completion_tokens += exc.completion_tokens
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
                "docs_sources": [source.model_dump() for source in pair.docs_sources],
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
        names, evidence = prefilter.symbol_overlap(
            symbols[pair.branch_a], symbols[pair.branch_b], pair.branch_a, pair.branch_b
        )
        signals[(pair.branch_a, pair.branch_b)] = prefilter.PairSignal(
            shared_files=pair.shared_files,
            overlapping_files=pair.overlapping_files,
            symbols=names,
            evidence=evidence,
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


def _with_static_checks(repo: Path, base_branch: str, pair: PairRisk) -> PairRisk:
    if not pair.prefilter_candidate or not pair.symbol_evidence:
        return pair
    try:
        checks = prefilter.static_checks(repo, base_branch, pair.symbol_evidence)
    except git_service.GitError:
        return pair
    return pair.model_copy(
        update={
            "static_breaks": [c.label for c in checks if c.verdict == "breaks"],
            "static_compatible": [c.label for c in checks if c.verdict == "compatible"],
        }
    )


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
    eligible = [p for p in pairs if verifier.is_high_risk(p)]
    eligible.sort(key=lambda p: (not p.static_breaks, -p.score))
    chosen = eligible[:limit]
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
        out.append(apply_verification(pair, result))
    return out


CONFIRMED_NOTE = "raised: confirmed by test"
BRANCH_TEST_NOTE = "raised: confirmed by branch test"
BOTH_NOTE = "raised: confirmed by branch test and intent test"
# High starts at 70. One point under keeps the level at medium.
CLEAN_MERGE_CAP = 69.0
CLEAN_MERGE_NOTE = "capped: clean git merge without a high model assessment"


def apply_verification(pair: PairRisk, result: Verification) -> PairRisk:
    """Attach a verdict. ``confirmed`` is authoritative: level ``high`` and the
    shown score floored at ``verify_confirmed_floor`` (git/model scores are kept
    as-is). Any other status keeps the blended score."""
    update: dict = {"verification": result}
    # The risk prompt may already have recorded the same lookup. Don't clear
    # it when verify did not search (no Python, or Tavily was off).
    if result.docs_sources:
        update["docs_sources"] = result.docs_sources
    if result.status == "confirmed":
        floor = float(settings.verify_confirmed_floor)
        update.update(
            {
                "blended_score": pair.score,
                "score": round(min(max(pair.score, floor), 100.0), 1),
                "level": "high",
                "score_note": CONFIRMED_NOTE,
            }
        )
    return pair.model_copy(update=update)


def apply_branch_tests(pair: PairRisk, result: BranchTests) -> PairRisk:
    """Attach the branch-test result. ``confirmed`` is authoritative like an
    intent-test confirmation: level high, score floored at
    ``verify_confirmed_floor``; the note says which layer(s) confirmed."""
    update: dict = {"branch_tests": result}
    if result.status == "confirmed":
        floor = float(settings.verify_confirmed_floor)
        intent = pair.verification is not None and pair.verification.status == "confirmed"
        # The clean-merge cap lowers ``score`` and keeps the blend in
        # ``blended_score``. Floor the higher of the two so a blend already
        # above the floor is not pulled down to it.
        basis = pair.score if pair.blended_score is None else max(pair.score, pair.blended_score)
        update.update(
            {
                "blended_score": pair.blended_score if pair.blended_score is not None else pair.score,
                "score": round(min(max(basis, floor), 100.0), 1),
                "level": "high",
                "score_note": BOTH_NOTE if intent else BRANCH_TEST_NOTE,
            }
        )
    return pair.model_copy(update=update)


def _run_branch_tests(repo: Path, base_branch: str, pairs: list[PairRisk]) -> dict:
    """Stage 0, no LLM: each candidate pair's own test files on the merge."""
    results: dict[tuple[str, str], BranchTests] = {}
    budget = float(settings.branch_tests_total_seconds)
    started = time.monotonic()
    for pair in branch_tests.eligible_pairs(pairs):
        key = (pair.branch_a, pair.branch_b)
        if time.monotonic() - started >= budget:
            results[key] = BranchTests(
                status="skipped",
                reason=f"branch-tests stage time budget ({budget:.0f}s) used up by earlier pairs",
            )
            continue
        results[key] = branch_tests.check_pair(repo, base_branch, pair.branch_a, pair.branch_b)
    return results


def _cap_clean_merge(pair: PairRisk) -> PairRisk:
    """High on a clean merge needs the model itself to have said high.

    Called after verify-by-test and before branch tests. A verification that
    already confirmed raised the uncapped score and is left alone. Any other
    clean pair is capped at ``CLEAN_MERGE_CAP`` unless ``score_source`` is
    ``model`` and ``model_level`` is ``high``. The note is set only when the
    score changes. ``textual_conflict`` true or unknown is unchanged.
    """
    if pair.textual_conflict is not False:
        return pair
    if pair.score_source == "model" and pair.model_level == "high":
        return pair
    if pair.verification is not None and pair.verification.status == "confirmed":
        return pair
    if pair.score <= CLEAN_MERGE_CAP:
        return pair
    score = min(pair.score, CLEAN_MERGE_CAP)
    return pair.model_copy(
        update={
            "blended_score": pair.score,
            "score": score,
            "level": _level(score),
            "score_note": CLEAN_MERGE_NOTE,
        }
    )


def _pair_finding(pair: PairRisk) -> Literal["textual_conflict", "semantic", "none"]:
    """Classify after scoring. A git conflict is not a semantic finding."""
    if pair.textual_conflict is True:
        return "textual_conflict"
    confirmed = (
        (pair.verification is not None and pair.verification.status == "confirmed")
        or (pair.branch_tests is not None and pair.branch_tests.status == "confirmed")
    )
    if pair.level in ("high", "medium") or confirmed:
        return "semantic"
    return "none"


def analyze(
    repo: Path,
    base_branch: str,
    branches: list[str],
    *,
    use_cache: bool = True,
    verify: bool = False,
    verify_allowed: bool = False,
    focus: str | None = None,
) -> AnalyzeResponse:
    """Score every branch pair (only pairs that include ``focus`` if given).

    ``verify`` runs the branch tests on the merge (no LLM, every candidate
    pair) and verify-by-test on high-risk model-scored pairs.
    ``verify_allowed`` must be True for any test to execute: the caller
    decides it (local repo under the allowed roots, never a URL clone).
    """
    started = time.monotonic()
    meter = UsageMeter()
    diffs = [git_service.diff_against_base(repo, b, base_branch) for b in branches]
    diff_by_branch = {d.branch: d for d in diffs}

    pairs: list[PairRisk] = []
    for a, b in combinations(branches, 2):
        if focus is None or focus in (a, b):
            pairs.append(_score_pair(diff_by_branch[a], diff_by_branch[b]))
    pairs.sort(key=lambda p: p.score, reverse=True)
    # Shown in the risk prompt. Cache key stays SHAs + prompt version because
    # merge-tree is fixed by those SHAs; the blend reads it after the model.
    pairs = [
        p.model_copy(
            update={"textual_conflict": git_service.textual_conflict(repo, p.branch_a, p.branch_b)}
        )
        for p in pairs
    ]

    signals = _prefilter_signals(repo, diff_by_branch, pairs)
    pairs = [
        p.model_copy(
            update={
                "symbol_overlap": list(signals[(p.branch_a, p.branch_b)].symbols),
                "symbol_evidence": list(signals[(p.branch_a, p.branch_b)].evidence),
                "prefilter_candidate": signals[(p.branch_a, p.branch_b)].is_candidate,
            }
        )
        for p in pairs
    ]
    pairs = [_with_static_checks(repo, base_branch, p) for p in pairs]

    # Before and independent of the model: a low model score cannot suppress it.
    branch_results: dict[tuple[str, str], BranchTests] = {}
    if verify and verify_allowed and settings.branch_tests_enabled:
        branch_results = _run_branch_tests(repo, base_branch, pairs)

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
        pairs.sort(key=lambda p: p.score, reverse=True)

    # After verify-by-test: is_high_risk reads level and score, and the cap
    # (69) sits under verify_min_score (70), so doing this earlier would shrink
    # who gets verified. Before branch tests, which still override a confirm.
    # A verification confirm is not recapped; it already used the uncapped score.
    pairs = [_cap_clean_merge(p) for p in pairs]
    pairs.sort(key=lambda p: p.score, reverse=True)

    if branch_results:
        pairs = [
            apply_branch_tests(p, branch_results[(p.branch_a, p.branch_b)])
            if (p.branch_a, p.branch_b) in branch_results else p
            for p in pairs
        ]
        pairs.sort(key=lambda p: p.score, reverse=True)

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
        branch_test_pairs=sum(
            1 for p in pairs
            if p.branch_tests and p.branch_tests.status in ("confirmed", "passed", "error")
        ),
        branch_test_confirmed=sum(
            1 for p in pairs if p.branch_tests and p.branch_tests.status == "confirmed"
        ),
        elapsed_s=round(time.monotonic() - started, 2),
    )
    pairs = [p.model_copy(update={"finding": _pair_finding(p)}) for p in pairs]
    return AnalyzeResponse(
        base_branch=base_branch,
        branches=branches,
        diffs=diffs,
        pairs=pairs,
        risk_model=risk_model,
        usage=usage,
    )
