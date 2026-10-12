"""The MergeGuard tools, independent of the MCP transport.

Each tool runs the engine in-process (``app.services.sentinel.analyze``) on
a local repository and returns plain JSON-able dicts. Guardrails:

- only local git repositories under the allowed roots: the bundled ``demo/``
  folder plus ``VERIFY_ALLOWED_ROOTS`` (os.pathsep-separated), the same rule
  that gates verify-by-test in the HTTP API;
- branch names must be existing local branches (no options, no revisions);
- one analysis at a time;
- outputs never contain API keys (configured keys are scrubbed from every
  string as a last line of defence) or generated test code / raw test output.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.schemas import AnalyzeResponse, PairRisk
from app.services import git_service, sentinel, verifier

Analyzer = Callable[..., AnalyzeResponse]

MAX_EVIDENCE = 5
_LOCK = threading.Lock()
_RISKY_LEVELS = ("medium", "high")
_LEVEL_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"\bsk-(?=[A-Za-z_\-]*\d)[A-Za-z0-9_\-]{20,}"),
]


class ToolInputError(ValueError):
    """A request the tool refuses, with a message meant for the caller."""


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


def allowed_roots() -> list[Path]:
    return verifier.allowed_roots()


def resolve_repo(repo_path: str) -> Path:
    if not repo_path or not repo_path.strip():
        raise ToolInputError("repo_path is required (absolute path of a local git repository)")
    if "://" in repo_path or repo_path.strip().startswith("git@"):
        raise ToolInputError("only local repository paths are supported, not URLs")
    raw = Path(repo_path.strip()).expanduser()
    if not raw.is_absolute():
        raise ToolInputError(f"repo_path must be absolute, got {repo_path!r}")
    repo = raw.resolve()
    if not verifier.repo_allowed(repo):
        roots = ", ".join(str(r) for r in allowed_roots())
        raise ToolInputError(
            f"{repo} is outside the allowed roots ({roots}). Add its parent folder to "
            "VERIFY_ALLOWED_ROOTS in the MCP server's environment to allow it."
        )
    if not repo.is_dir():
        raise ToolInputError(f"{repo} does not exist")
    if not (repo / ".git").exists():
        raise ToolInputError(f"{repo} is not a git repository (no .git)")
    return repo


def _branches(repo: Path) -> list[str]:
    try:
        return git_service.list_branches(repo)
    except git_service.GitError as exc:
        raise ToolInputError(f"git could not list branches: {exc}") from exc


def check_branch(name: str, existing: list[str], what: str) -> str:
    if not name or name.startswith("-") or name != name.strip():
        raise ToolInputError(f"invalid {what} name {name!r}")
    if name not in existing:
        sample = ", ".join(existing[:20]) + (" ..." if len(existing) > 20 else "")
        raise ToolInputError(f"{what} {name!r} is not a local branch. Branches: {sample}")
    return name


# ---------------------------------------------------------------------------
# Output shaping
# ---------------------------------------------------------------------------


def _secrets() -> list[str]:
    return [k for k in (settings.nebius_api_key, settings.tavily_api_key) if k and len(k) >= 8]


def scrub(value: Any) -> Any:
    """Remove configured keys and key-shaped tokens from every string."""
    if isinstance(value, str):
        for secret in _secrets():
            value = value.replace(secret, "[redacted]")
        for pattern in _SECRET_PATTERNS:
            value = pattern.sub("[redacted]", value)
        return value
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def confirmed_by(pair: PairRisk) -> list[str]:
    """Which layers confirmed the pair: the branches' own tests (deterministic,
    no LLM) and/or the Nemotron-generated intent test."""
    layers = []
    if pair.branch_tests is not None and pair.branch_tests.status == "confirmed":
        layers.append("branch test")
    if pair.verification is not None and pair.verification.status == "confirmed":
        layers.append("intent test")
    return layers


def verdict(pair: PairRisk) -> tuple[str, str]:
    v = pair.verification
    bt = pair.branch_tests
    if bt is not None and bt.status == "confirmed":
        reason = "branch test (existing tests, no LLM): " + bt.reason
        if v is not None and v.status == "confirmed":
            reason += "; intent test (Nemotron): " + v.reason
        return "confirmed", reason
    if v is None:
        return "not verified", "no generated test was run for this pair"
    if v.status == "skipped":
        return "not verified", v.reason
    return v.status, v.reason


def pair_summary(pair: PairRisk, focus: str | None = None) -> dict[str, Any]:
    status, reason = verdict(pair)
    v = pair.verification
    out: dict[str, Any] = {
        "branch_a": pair.branch_a,
        "branch_b": pair.branch_b,
    }
    if focus is not None:
        out["other_branch"] = pair.branch_b if pair.branch_a == focus else pair.branch_a
    out.update(
        level=pair.level,
        score=round(pair.score, 1),
        score_source="nemotron" if pair.score_source == "model" else "git heuristic",
        verdict=status,
        verdict_reason=reason,
        rationale=pair.rationale,
        symbol_evidence=list(pair.symbol_evidence[:MAX_EVIDENCE]),
        static_breaks=list(pair.static_breaks),
        unsupported_claims=list(pair.unsupported_claims),
        overlapping_files=pair.overlapping_files,
        textual_merge_conflict=bool((v and v.merge_conflict)
                                    or (pair.branch_tests and pair.branch_tests.merge_conflict)),
        confirmed_by=confirmed_by(pair),
        confirming_tests=list(v.confirming_tests) if v else [],
        branch_test_failures=[
            {"test_id": f.test_id, "branch": f.branch, "excerpt": f.excerpt}
            for f in (pair.branch_tests.confirming if pair.branch_tests else [])
        ],
        tokens=(pair.prompt_tokens + pair.completion_tokens
                + ((v.prompt_tokens + v.completion_tokens) if v else 0)),
    )
    return out


def cost_summary(res: AnalyzeResponse) -> dict[str, Any]:
    u = res.usage
    if u is None:
        return {"llm_calls": 0, "total_tokens": 0, "estimated_cost_usd": 0.0}
    return {
        "model": res.risk_model or u.model,
        "llm_calls": u.llm_calls,
        "cache_hits": u.cache_hits,
        "total_tokens": u.total_tokens,
        "estimated_cost_usd": round(u.estimated_cost_usd, 6),
        "pairs_sent_to_model": u.pairs_sent_to_model,
        "pairs_verified": u.verify_pairs,
        "pairs_branch_tested": u.branch_test_pairs,
        "elapsed_s": u.elapsed_s,
    }


def _risk_key(p: dict[str, Any]) -> tuple:
    """Rank: confirmed first, then suspected, then by level and score."""
    order = {"confirmed": 0, "suspected": 1, "error": 2, "not verified": 3}
    return (order.get(p["verdict"], 3), -_LEVEL_RANK.get(p["level"], 0), -p["score"],
            p["branch_a"], p["branch_b"])


def is_risky(p: dict[str, Any]) -> bool:
    return (p["verdict"] in ("confirmed", "suspected") or p["level"] in _RISKY_LEVELS
            or p["textual_merge_conflict"] or bool(p["static_breaks"]))


def _model_note() -> str | None:
    if not settings.nebius_ready:
        return ("NEBIUS_API_KEY is not set for the MCP server, so scores come from the git "
                "heuristic only and nothing is verified by test.")
    return None


# ---------------------------------------------------------------------------
# Engine call
# ---------------------------------------------------------------------------


def _run(analyzer: Analyzer, repo: Path, base: str, branches: list[str], *, verify: bool,
         use_cache: bool, focus: str | None = None) -> AnalyzeResponse:
    if not _LOCK.acquire(timeout=1):
        raise ToolInputError("another MergeGuard analysis is running; try again when it finishes")
    try:
        kwargs: dict[str, Any] = dict(use_cache=use_cache, verify=verify,
                                      verify_allowed=verifier.repo_allowed(repo))
        if focus is not None:
            kwargs["focus"] = focus
        return analyzer(repo, base, branches, **kwargs)
    except git_service.GitError as exc:
        raise ToolInputError(f"git: {exc}") from exc
    finally:
        _LOCK.release()


def _prepare(repo_path: str, base: str) -> tuple[Path, str, list[str]]:
    repo = resolve_repo(repo_path)
    existing = _branches(repo)
    check_branch(base, existing, "base branch")
    others = [b for b in existing if b != base]
    if not others:
        raise ToolInputError(f"no branches other than {base!r} to compare")
    return repo, base, others


def _envelope(repo: Path, base: str, res: AnalyzeResponse, **body: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"repo": str(repo), "base": base, **body, "cost": cost_summary(res)}
    note = _model_note()
    if note:
        out["note"] = note
    return scrub(out)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def check_merge_risk(repo_path: str, branch: str, base: str = "main", *, verify: bool = True,
                     use_cache: bool = True, analyzer: Analyzer | None = None) -> dict[str, Any]:
    repo, base, others = _prepare(repo_path, base)
    check_branch(branch, others, "branch")
    if len(others) < 2:
        raise ToolInputError(f"{branch!r} is the only branch besides {base!r}; nothing to compare")
    res = _run(analyzer or sentinel.analyze, repo, base, others, verify=verify,
               use_cache=use_cache, focus=branch)
    pairs = sorted((pair_summary(p, focus=branch) for p in res.pairs
                    if branch in (p.branch_a, p.branch_b)), key=_risk_key)
    risky = [p for p in pairs if is_risky(p)]
    if any(p["verdict"] == "confirmed" for p in pairs):
        headline = (f"{branch} has a test-confirmed semantic conflict with "
                    + ", ".join(p["other_branch"] for p in pairs if p["verdict"] == "confirmed"))
    elif risky:
        headline = f"{branch} has {len(risky)} risky pair(s); none confirmed by a test"
    else:
        headline = f"no risky pair found for {branch} against {len(pairs)} other branch(es)"
    return _envelope(repo, base, res, branch=branch, summary=headline, pairs=pairs)


def list_conflicting_branches(repo_path: str, base: str = "main", *, verify: bool = True,
                              use_cache: bool = True,
                              analyzer: Analyzer | None = None) -> dict[str, Any]:
    repo, base, others = _prepare(repo_path, base)
    if len(others) < 2:
        raise ToolInputError("need at least two branches besides the base to compare")
    res = _run(analyzer or sentinel.analyze, repo, base, others, verify=verify, use_cache=use_cache)
    pairs = sorted((pair_summary(p) for p in res.pairs), key=_risk_key)
    risky = [p for p in pairs if is_risky(p)]
    return _envelope(
        repo, base, res,
        summary=(f"{len(risky)} risky pair(s) of {len(pairs)}; "
                 f"{sum(p['verdict'] == 'confirmed' for p in risky)} confirmed by a test"),
        risky_pairs=risky,
        pairs_checked=len(pairs),
    )


def merge_order(branches: list[str], pairs: list[dict[str, Any]], base: str) -> dict[str, Any]:
    """Deterministic, explainable merge order from pairwise results.

    1. Branches with no risky pair: merge first (alphabetical; any order works).
    2. Branches whose risky pairs are unconfirmed estimates: next, fewest risky
       pairs first, then lowest worst score, then name.
    3. Branches in a test-confirmed conflict: last, one at a time. Of each
       confirmed pair, the branch with fewer risky pairs (then name) goes first;
       the other is rebased onto the updated base and fixed before it merges.
    """
    risky = [p for p in pairs if is_risky(p)]
    confirmed = [p for p in risky if p["verdict"] == "confirmed"]
    partners: dict[str, list[dict[str, Any]]] = {b: [] for b in branches}
    for p in risky:
        for b in (p["branch_a"], p["branch_b"]):
            partners.setdefault(b, []).append(p)
    in_confirmed = {b for p in confirmed for b in (p["branch_a"], p["branch_b"])}

    def weight(b: str) -> tuple:
        return (len(partners[b]), max((p["score"] for p in partners[b]), default=0.0), b)

    safe = sorted(b for b in branches if not partners[b])
    caution = sorted((b for b in branches if partners[b] and b not in in_confirmed), key=weight)
    conflict = sorted(in_confirmed, key=weight)

    steps: list[dict[str, Any]] = []
    merged: set[str] = set()

    def other(p: dict[str, Any], b: str) -> str:
        return p["branch_b"] if p["branch_a"] == b else p["branch_a"]

    for b in safe:
        steps.append({"branch": b, "action": "merge",
                      "why": "no risky pair with any other branch"})
        merged.add(b)
    for b in caution:
        names = ", ".join(sorted(other(p, b) for p in partners[b]))
        steps.append({
            "branch": b, "action": "merge, then re-run the tests of: " + names,
            "why": (f"{len(partners[b])} risky pair(s) ({names}), none confirmed by a test; "
                    f"worst score {weight(b)[1]:.0f}"),
        })
        merged.add(b)
    for b in conflict:
        mine = [p for p in confirmed if b in (p["branch_a"], p["branch_b"])]
        earlier = sorted(other(p, b) for p in mine if other(p, b) in merged)
        if earlier:
            tests = sorted({t for p in mine if other(p, b) in merged for t in p["confirming_tests"]})
            steps.append({
                "branch": b,
                "action": f"rebase onto {base} after {', '.join(earlier)} is merged, fix the "
                          "conflict, re-run check_merge_risk, then merge",
                "why": ("test-confirmed semantic conflict with " + ", ".join(earlier)
                        + (f" (failing intent tests: {', '.join(tests)})" if tests else "")),
            })
        else:
            later = sorted(other(p, b) for p in mine)
            mine_w, theirs = weight(b), [weight(o) for o in later]
            if all(mine_w[0] < t[0] for t in theirs):
                because = (f"it has fewer risky pairs ({mine_w[0]} vs "
                           + ", ".join(str(t[0]) for t in theirs) + ")")
            elif all(mine_w[0] == t[0] and mine_w[1] < t[1] for t in theirs):
                because = (f"same number of risky pairs, lower worst score "
                           f"({mine_w[1]:.0f} vs " + ", ".join(f"{t[1]:.0f}" for t in theirs) + ")")
            else:
                because = ("the risk evidence does not favour either branch; the tie is "
                           "broken by name, so either order works")
            steps.append({
                "branch": b, "action": "merge alone (one at a time)",
                "why": (f"test-confirmed conflict with {', '.join(later)}; goes first because "
                        f"{because}. The other branch then adapts to it"),
            })
        merged.add(b)
    for i, step in enumerate(steps, 1):
        step["step"] = i
    return {
        "order": [{"step": s["step"], "branch": s["branch"], "action": s["action"],
                   "why": s["why"]} for s in steps],
        "rule": ("no-risk branches first; then unconfirmed risky ones (fewest risky pairs, "
                 "lowest worst score); test-confirmed conflicts last, one at a time, the "
                 "partner rebased and fixed before it merges"),
        "caveats": [
            "Pairwise analysis only: it does not prove the combined result of three or more "
            "branches is correct.",
            "'not verified' and 'suspected' pairs are risk estimates, not proof.",
        ],
    }


def suggest_merge_order(repo_path: str, base: str = "main", *, verify: bool = True,
                        use_cache: bool = True,
                        analyzer: Analyzer | None = None) -> dict[str, Any]:
    repo, base, others = _prepare(repo_path, base)
    if len(others) < 2:
        res_pairs: list[dict[str, Any]] = []
        res = None
    else:
        res = _run(analyzer or sentinel.analyze, repo, base, others, verify=verify,
                   use_cache=use_cache)
        res_pairs = [pair_summary(p) for p in res.pairs]
    plan = merge_order(others, res_pairs, base)
    risky = sorted((p for p in res_pairs if is_risky(p)), key=_risk_key)
    body = dict(plan, risky_pairs=[
        {k: p[k] for k in ("branch_a", "branch_b", "level", "score", "verdict", "verdict_reason")}
        for p in risky
    ])
    if res is None:
        return scrub({"repo": str(repo), "base": base, **body,
                      "cost": {"llm_calls": 0, "total_tokens": 0, "estimated_cost_usd": 0.0}})
    return _envelope(repo, base, res, **body)
