"""MergeGuard MCP tools with a mocked engine (no model calls, no mcp SDK needed)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.schemas import AnalyzeResponse, PairRisk, UsageSummary, Verification
from mergeguard_mcp import tools


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "allowed"
    r = root / "shop"
    r.mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    for b in ("feat/cap", "feat/tax", "docs/readme", "feat/log"):
        _git(r, "branch", b)
    monkeypatch.setattr(settings, "verify_allowed_roots", str(root))
    monkeypatch.setattr(settings, "nebius_api_key", "nb-SECRET-test-key-0123456789")
    return r


def _pair(a: str, b: str, score: float, level: str, status: str | None = None, **extra) -> PairRisk:
    v = None
    if status:
        v = Verification(status=status, reason=f"{status} reason",  # type: ignore[arg-type]
                         confirming_tests=["test_branch_b_total"] if status == "confirmed" else [],
                         prompt_tokens=100, completion_tokens=50)
    data = dict(branch_a=a, branch_b=b, score=score, level=level, shared_files=1,
                overlapping_files=0, collisions=[], rationale=f"{a} vs {b}",
                score_source="model", model_score=score, prompt_tokens=10, completion_tokens=5,
                verification=v)
    data.update(extra)
    return PairRisk(**data)


PAIRS = [
    _pair("feat/cap", "feat/tax", 92, "high", "confirmed",
          symbol_evidence=["total: changed by feat/cap (shop.py), called by feat/tax (cart.py)"]),
    _pair("feat/log", "feat/tax", 55, "medium"),
    _pair("docs/readme", "feat/cap", 5, "low"),
    _pair("docs/readme", "feat/tax", 5, "low"),
    _pair("docs/readme", "feat/log", 0, "none"),
    _pair("feat/cap", "feat/log", 10, "low", "skipped"),
]


class FakeEngine:
    def __init__(self, pairs=PAIRS):
        self.calls: list[dict] = []
        self.pairs = pairs

    def __call__(self, repo, base, branches, **kwargs):
        self.calls.append(dict(repo=repo, base=base, branches=branches, **kwargs))
        focus = kwargs.get("focus")
        pairs = [p for p in self.pairs if focus is None or focus in (p.branch_a, p.branch_b)]
        usage = UsageSummary(model="m", llm_calls=3, total_tokens=1234, estimated_cost_usd=0.0021,
                             input_usd_per_1m=0.3, output_usd_per_1m=0.9, elapsed_s=1.5)
        return AnalyzeResponse(base_branch=base, branches=branches, diffs=[], pairs=pairs,
                               risk_model="m", usage=usage)


def test_check_merge_risk_ranks_pairs_of_the_branch(repo: Path) -> None:
    engine = FakeEngine()
    out = tools.check_merge_risk(str(repo), "feat/tax", analyzer=engine)
    call = engine.calls[0]
    assert call["focus"] == "feat/tax" and call["verify"] is True and call["verify_allowed"] is True
    assert sorted(call["branches"]) == ["docs/readme", "feat/cap", "feat/log", "feat/tax"]
    assert [p["other_branch"] for p in out["pairs"]] == ["feat/cap", "feat/log", "docs/readme"]
    top = out["pairs"][0]
    assert top["verdict"] == "confirmed" and top["level"] == "high" and top["score"] == 92
    assert top["confirming_tests"] == ["test_branch_b_total"]
    assert top["symbol_evidence"][0].startswith("total: changed by feat/cap")
    assert top["tokens"] == 165
    assert out["pairs"][1]["verdict"] == "not verified"
    assert out["summary"] == "feat/tax has a test-confirmed semantic conflict with feat/cap"
    assert out["cost"]["estimated_cost_usd"] == 0.0021 and out["cost"]["total_tokens"] == 1234
    assert "test_code" not in str(out) and "note" not in out


def test_list_conflicting_branches_only_returns_risky_pairs(repo: Path) -> None:
    out = tools.list_conflicting_branches(str(repo), analyzer=FakeEngine())
    assert [(p["branch_a"], p["branch_b"]) for p in out["risky_pairs"]] == [
        ("feat/cap", "feat/tax"), ("feat/log", "feat/tax")]
    assert out["pairs_checked"] == 6
    assert out["summary"] == "2 risky pair(s) of 6; 1 confirmed by a test"


def test_suggest_merge_order_is_deterministic_and_explained(repo: Path) -> None:
    out = tools.suggest_merge_order(str(repo), analyzer=FakeEngine())
    order = [(s["branch"], s["action"]) for s in out["order"]]
    assert order == [
        ("docs/readme", "merge"),
        ("feat/log", "merge, then re-run the tests of: feat/tax"),
        ("feat/cap", "merge alone (one at a time)"),
        ("feat/tax", "rebase onto main after feat/cap is merged, fix the conflict, "
                     "re-run check_merge_risk, then merge"),
    ]
    assert "test_branch_b_total" in out["order"][3]["why"]
    assert out["order"][2]["why"] == (
        "test-confirmed conflict with feat/tax; goes first because it has fewer risky pairs "
        "(1 vs 2). The other branch then adapts to it")
    assert out == tools.suggest_merge_order(str(repo), analyzer=FakeEngine())  # deterministic
    assert out["risky_pairs"][0]["verdict"] == "confirmed"


def test_merge_order_tie_break_prefers_fewer_risky_pairs() -> None:
    def p(a, b, verdict="confirmed"):
        return {"branch_a": a, "branch_b": b, "level": "high", "score": 90.0, "verdict": verdict,
                "static_breaks": [], "textual_merge_conflict": False, "confirming_tests": []}
    # z conflicts with both a and m; a and m only with z: a, m go first, z adapts last.
    plan = tools.merge_order(["a", "m", "z"], [p("a", "z"), p("m", "z")], "main")
    assert [s["branch"] for s in plan["order"]] == ["a", "m", "z"]
    assert plan["order"][2]["action"].startswith("rebase onto main after a, m is merged")
    assert "fewer risky pairs (1 vs 2)" in plan["order"][0]["why"]
    tie = tools.merge_order(["z", "a"], [p("a", "z")], "main")["order"]
    assert [s["branch"] for s in tie] == ["a", "z"]
    assert "the tie is broken by name, so either order works" in tie[0]["why"]


@pytest.mark.parametrize("path,fragment", [
    ("", "repo_path is required"),
    ("relative/repo", "must be absolute"),
    ("https://github.com/x/y.git", "not URLs"),
    ("/etc", "outside the allowed roots"),
])
def test_guardrails_reject_bad_paths(repo: Path, path: str, fragment: str) -> None:
    engine = FakeEngine()
    with pytest.raises(tools.ToolInputError, match=fragment):
        tools.list_conflicting_branches(path, analyzer=engine)
    assert engine.calls == []


def test_guardrails_reject_non_git_and_unknown_branches(repo: Path) -> None:
    plain = repo.parent / "plain"
    plain.mkdir()
    with pytest.raises(tools.ToolInputError, match="not a git repository"):
        tools.list_conflicting_branches(str(plain), analyzer=FakeEngine())
    with pytest.raises(tools.ToolInputError, match="is not a local branch"):
        tools.check_merge_risk(str(repo), "feat/nope", analyzer=FakeEngine())
    with pytest.raises(tools.ToolInputError, match="invalid branch name"):
        tools.check_merge_risk(str(repo), "--upload-pack=x", analyzer=FakeEngine())
    with pytest.raises(tools.ToolInputError, match="base branch 'trunk' is not a local branch"):
        tools.list_conflicting_branches(str(repo), base="trunk", analyzer=FakeEngine())
    # A symlink inside the allowed root that points outside it is refused.
    link = repo.parent / "escape"
    link.symlink_to("/")
    with pytest.raises(tools.ToolInputError, match="outside the allowed roots"):
        tools.list_conflicting_branches(str(link), analyzer=FakeEngine())


def test_outputs_never_contain_the_api_key(repo: Path) -> None:
    leaky = [_pair("feat/cap", "feat/tax", 80, "high",
                   rationale="uses nb-SECRET-test-key-0123456789 and Bearer abcdefghijklmnop1234")]
    out = tools.check_merge_risk(str(repo), "feat/cap", analyzer=FakeEngine(leaky))
    text = str(out)
    assert "nb-SECRET" not in text and "abcdefghijklmnop1234" not in text
    assert "[redacted]" in out["pairs"][0]["rationale"]


def test_no_key_note(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "")
    out = tools.list_conflicting_branches(str(repo), analyzer=FakeEngine())
    assert "git heuristic only" in out["note"]


def test_engine_focus_scores_only_pairs_with_the_branch(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import sentinel

    monkeypatch.setattr(settings, "nebius_enabled", False)
    branches = ["docs/readme", "feat/cap", "feat/log", "feat/tax"]
    res = sentinel.analyze(repo, "main", branches, focus="feat/tax")
    assert sorted((p.branch_a, p.branch_b) for p in res.pairs) == sorted(
        tuple(sorted(("feat/tax", b))) for b in ("docs/readme", "feat/cap", "feat/log"))
    assert len(sentinel.analyze(repo, "main", branches).pairs) == 6


def test_pair_summary_labels_a_branch_test_confirmation() -> None:
    from app.schemas import BranchTestFailure, BranchTests

    bt = BranchTests(status="confirmed", reason="1 own test fails on the merge",
                     confirming=[BranchTestFailure(test_id="tests.test_r::test_x", branch="feat/tax",
                                                   file="tests/test_r.py", excerpt="E   KeyError")])
    only_branch = tools.pair_summary(_pair("feat/log", "feat/tax", 90, "high", "suspected",
                                           branch_tests=bt))
    assert only_branch["verdict"] == "confirmed"
    assert only_branch["confirmed_by"] == ["branch test"]
    assert only_branch["verdict_reason"].startswith("branch test (existing tests, no LLM)")
    assert only_branch["branch_test_failures"] == [
        {"test_id": "tests.test_r::test_x", "branch": "feat/tax", "excerpt": "E   KeyError"}]
    both = tools.pair_summary(_pair("feat/cap", "feat/tax", 92, "high", "confirmed", branch_tests=bt))
    assert both["confirmed_by"] == ["branch test", "intent test"]
    intent = tools.pair_summary(PAIRS[0])
    assert intent["confirmed_by"] == ["intent test"] and intent["branch_test_failures"] == []
