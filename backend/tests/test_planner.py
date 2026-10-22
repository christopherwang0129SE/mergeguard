"""Tests for the Planner agent (app.services.planner and POST /api/plan)."""
from __future__ import annotations

from typing import Any

import pytest

from app.api import routes
from app.schemas import AnalyzeResponse, BranchDiff, PairRisk
from app.services import planner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pair(a: str, b: str, level: str, score: float = 50.0) -> PairRisk:
    return PairRisk(
        branch_a=a,
        branch_b=b,
        score=score,
        level=level,
        shared_files=1,
        overlapping_files=1 if level != "none" else 0,
        collisions=[],
        rationale=f"{level} risk between {a} and {b}",
    )


def _analysis(
    branches: list[str],
    pairs: list[PairRisk],
    base: str = "main",
) -> AnalyzeResponse:
    diffs = [
        BranchDiff(branch=b, base=base, merge_base=None, ahead=1, files=[])
        for b in branches
    ]
    return AnalyzeResponse(base_branch=base, branches=branches, diffs=diffs, pairs=pairs)


# ---------------------------------------------------------------------------
# Unit tests: planner.plan()
# ---------------------------------------------------------------------------


def test_plan_with_no_branches_returns_empty_plan() -> None:
    analysis = _analysis([], [])
    result = planner.plan(analysis)
    assert result.steps == []
    assert result.zones == []
    assert result.summary == "No branches to plan."
    assert result.parallelisable is False


def test_single_safe_branch_is_safe() -> None:
    analysis = _analysis(["feat/a"], [])
    result = planner.plan(analysis)
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.branch == "feat/a"
    assert step.position == 1
    assert step.zone == 1
    assert step.status == "safe"
    assert step.conflicts_with == []
    assert result.parallelisable is False


def test_two_independent_branches_are_parallelisable() -> None:
    analysis = _analysis(
        ["feat/a", "feat/b"],
        [_pair("feat/a", "feat/b", "none", score=0)],
    )
    result = planner.plan(analysis)
    assert result.parallelisable is True
    assert len(result.zones) == 2
    statuses = {s.branch: s.status for s in result.steps}
    assert statuses["feat/a"] == "safe"
    assert statuses["feat/b"] == "safe"


def test_high_risk_pair_puts_both_branches_in_same_zone() -> None:
    analysis = _analysis(
        ["feat/a", "feat/b"],
        [_pair("feat/a", "feat/b", "high", score=80)],
    )
    result = planner.plan(analysis)
    zones = {s.branch: s.zone for s in result.steps}
    assert zones["feat/a"] == zones["feat/b"]
    assert result.parallelisable is False


def test_danger_status_assigned_to_high_risk_branch() -> None:
    analysis = _analysis(
        ["feat/a", "feat/b"],
        [_pair("feat/a", "feat/b", "high", score=80)],
    )
    result = planner.plan(analysis)
    statuses = {s.branch: s.status for s in result.steps}
    assert statuses["feat/a"] == "danger"
    assert statuses["feat/b"] == "danger"


def test_medium_risk_pair_gives_caution_status() -> None:
    analysis = _analysis(
        ["feat/a", "feat/b"],
        [_pair("feat/a", "feat/b", "medium", score=50)],
    )
    result = planner.plan(analysis)
    statuses = {s.branch: s.status for s in result.steps}
    assert statuses["feat/a"] == "caution"
    assert statuses["feat/b"] == "caution"


def test_conflicts_with_lists_medium_and_high_peers() -> None:
    analysis = _analysis(
        ["feat/a", "feat/b", "feat/c"],
        [
            _pair("feat/a", "feat/b", "high", score=80),
            _pair("feat/a", "feat/c", "medium", score=50),
            _pair("feat/b", "feat/c", "low", score=10),
        ],
    )
    result = planner.plan(analysis)
    by_branch = {s.branch: s for s in result.steps}
    assert "feat/b" in by_branch["feat/a"].conflicts_with
    assert "feat/c" in by_branch["feat/a"].conflicts_with
    # feat/b and feat/c are only low risk — not listed as conflicts for each other.
    assert "feat/c" not in by_branch["feat/b"].conflicts_with


def test_positions_are_sequential_starting_from_one() -> None:
    analysis = _analysis(
        ["feat/a", "feat/b", "feat/c"],
        [_pair("feat/a", "feat/b", "high", score=80)],
    )
    result = planner.plan(analysis)
    positions = sorted(s.position for s in result.steps)
    assert positions == [1, 2, 3]


def test_higher_risk_branch_gets_lower_position_within_zone() -> None:
    # feat/a has two high-risk pairs; feat/b and feat/c have one each.
    analysis = _analysis(
        ["feat/a", "feat/b", "feat/c"],
        [
            _pair("feat/a", "feat/b", "high", score=90),
            _pair("feat/a", "feat/c", "high", score=90),
        ],
    )
    result = planner.plan(analysis)
    by_branch = {s.branch: s for s in result.steps}
    # feat/a has risk_score 20 (two high edges × 10); feat/b and feat/c have 10 each.
    assert by_branch["feat/a"].position < by_branch["feat/b"].position
    assert by_branch["feat/a"].position < by_branch["feat/c"].position


def test_summary_mentions_danger_when_present() -> None:
    analysis = _analysis(
        ["feat/a", "feat/b"],
        [_pair("feat/a", "feat/b", "high", score=80)],
    )
    result = planner.plan(analysis)
    assert "danger" in result.summary


def test_summary_mentions_parallel_when_independent_zones() -> None:
    analysis = _analysis(
        ["feat/a", "feat/b"],
        [_pair("feat/a", "feat/b", "none", score=0)],
    )
    result = planner.plan(analysis)
    assert "parallel" in result.summary.lower()


# ---------------------------------------------------------------------------
# Route tests: POST /api/plan
# ---------------------------------------------------------------------------


def test_plan_endpoint_accepts_pre_computed_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = _analysis(
        ["feat/a", "feat/b"],
        [_pair("feat/a", "feat/b", "high", score=80)],
    )
    result = routes.plan(
        routes.PlanRequest(analysis=analysis)
    )
    assert result.base_branch == "main"
    assert set(result.branches) == {"feat/a", "feat/b"}
    assert len(result.steps) == 2
    assert result.parallelisable is False


def test_plan_endpoint_returns_step_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = _analysis(["feat/safe"], [])
    result = routes.plan(routes.PlanRequest(analysis=analysis))
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.branch == "feat/safe"
    assert step.status == "safe"
    assert step.position == 1
    assert step.conflicts_with == []


def test_plan_endpoint_raises_400_for_missing_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes.settings, "default_base_branch", "main")
    with pytest.raises(routes.HTTPException) as exc_info:
        routes.plan(routes.PlanRequest(repo_path=None, repo_url=None, analysis=None))
    assert exc_info.value.status_code == 400


def test_plan_endpoint_calls_sentinel_when_no_analysis(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    analysis = _analysis(["feat/x"], [])
    monkeypatch.setattr(routes.settings, "default_base_branch", "main")
    monkeypatch.setattr(routes.settings, "demo_mode", False)
    monkeypatch.setattr(routes.git_service, "clone", lambda *a, **kw: tmp_path)
    monkeypatch.setattr(routes.git_service, "list_branches", lambda *a, **kw: ["feat/x"])
    monkeypatch.setattr(routes.sentinel, "analyze", lambda *a, **kw: analysis)
    # Provide a fake repo_url so the route clones instead of looking up a local path.
    result = routes.plan(
        routes.PlanRequest(repo_url="https://github.com/example/repo.git")
    )
    assert result.branches == ["feat/x"]
    assert len(result.steps) == 1
