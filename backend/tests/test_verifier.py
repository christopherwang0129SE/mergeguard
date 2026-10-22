"""Verify-by-test runner: mocked LLM, real git worktrees and real pytest runs."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.schemas import PairRisk
from app.services import sentinel, verifier
from app.services.nemotron import LLMText
from app.services.usage import UsageMeter


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


CALC = '''def scale(x):
    return x * 2


def helper():
    return "unchanged"


def total(x):
    return scale(x) + 1
'''

GOOD_TEST = '''import calc


def test_branch_a_scale_triples():
    # branch A: scale(2) = 2 * 3 = 6
    assert calc.scale(2) == 6


def test_branch_b_total_adds_ten():
    # branch B: total(1) = scale(1) + 10 = 2 + 10 = 12
    assert calc.total(1) == 12
'''

WEAK_TEST = '''import calc


def test_branch_a_scale_zero():
    assert calc.scale(0) == 0


def test_branch_b_total_type():
    assert isinstance(calc.total(1), int)
'''

WRONG_ON_A = GOOD_TEST.replace("== 6", "== 7")
WRONG_ON_B = GOOD_TEST.replace("== 12", "== 99")
BOTH_WRONG = WRONG_ON_A.replace("== 12", "== 99")


@pytest.fixture()
def vrepo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "allowed" / "calc-repo"
    r.mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "calc.py").write_text(CALC)
    (r / "README.md").write_text("# calc\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    _git(r, "checkout", "-q", "-b", "feat/scale")
    (r / "calc.py").write_text(CALC.replace("return x * 2", "return x * 3"))
    _git(r, "commit", "-qam", "scale triples")
    _git(r, "checkout", "-q", "main")
    _git(r, "checkout", "-q", "-b", "feat/total")
    (r / "calc.py").write_text(CALC.replace("return scale(x) + 1", "return scale(x) + 10"))
    _git(r, "commit", "-qam", "total adds ten")
    _git(r, "checkout", "-q", "main")
    _git(r, "checkout", "-q", "-b", "docs/readme")
    (r / "README.md").write_text("# calc\n\nDocs.\n")
    _git(r, "commit", "-qam", "docs")
    _git(r, "checkout", "-q", "main")
    monkeypatch.setattr(settings, "verify_allowed_roots", str(tmp_path / "allowed"))
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    return r


def _pair(a: str = "feat/scale", b: str = "feat/total", **extra) -> PairRisk:
    data = dict(
        branch_a=a, branch_b=b, score=47.5, level="medium", shared_files=1,
        overlapping_files=0, collisions=[], rationale="r", score_source="model",
        git_score=10, model_score=85, model_level="high",
    )
    data.update(extra)
    return PairRisk(**data)


def _scripted(replies: list[str], prompts: list[str]):
    def fake_complete(prompt: str, **kwargs) -> str:
        prompts.append(prompt)
        assert kwargs.get("system_prompt") == verifier.SYSTEM_PROMPT
        code = replies[min(len(prompts) - 1, len(replies) - 1)]
        return LLMText(f"<think>plan</think>\n```python\n{code}```", 900, 300)

    return fake_complete


def _worktrees(repo: Path) -> list[str]:
    return [ln for ln in _git(repo, "worktree", "list", "--porcelain").splitlines()
            if ln.startswith("worktree ")]


def test_confirmed_when_a_and_b_pass_and_merge_fails(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([GOOD_TEST], prompts))
    meter = UsageMeter()
    result = verifier.verify_pair(vrepo, "main", _pair(), meter=meter)

    assert result.status == "confirmed", result.reason
    assert [r.target for r in result.runs] == ["a", "b", "base", "merge"]
    run_a, run_b, run_base, run_m = result.runs
    assert (run_a.passed, run_a.failed) == (1, 0)
    assert (run_b.passed, run_b.failed) == (1, 0)
    assert run_base.failed == 2  # both intents are real changes vs the base
    assert run_m.failed == 1 and run_m.passed == 1  # total(1) == 13 on the merge
    assert "13" in run_m.output
    assert result.confirming_tests == ["test_branch_b_total_adds_ten"]
    assert run_m.cases == {
        "test_branch_a_scale_triples": "passed",
        "test_branch_b_total_adds_ten": "failed",
    }
    assert result.test_code == GOOD_TEST
    assert result.test_path and Path(result.test_path).read_text() == GOOD_TEST
    assert json.loads((Path(result.test_path).parent / "result.json").read_text())["status"] == "confirmed"
    assert result.attempts == 1 and result.cached_test is False
    assert (result.prompt_tokens, result.completion_tokens) == (900, 300)
    assert meter.llm_calls == 1
    # The prompt carries both branches' code and commit intent.
    assert "scale triples" in prompts[0] and "total adds ten" in prompts[0]
    assert "return x * 3" in prompts[0] and "scale(x) + 10" in prompts[0]
    # Throwaway worktrees are gone; the repo itself is untouched.
    assert len(_worktrees(vrepo)) == 1
    assert _git(vrepo, "status", "--porcelain") == ""
    assert _git(vrepo, "branch", "--show-current").strip() == "main"


def test_second_run_reuses_cached_test_without_llm(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([GOOD_TEST], prompts))
    verifier.verify_pair(vrepo, "main", _pair())
    meter = UsageMeter()
    again = verifier.verify_pair(vrepo, "main", _pair(), meter=meter)
    assert len(prompts) == 1
    assert meter.llm_calls == 0 and meter.cache_hits == 1
    assert again.status == "confirmed" and again.cached_test is True


def test_repair_round_fixes_a_test_that_fails_on_its_own_branch(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(
        "app.services.nemotron.complete", _scripted([WRONG_ON_B, GOOD_TEST], prompts)
    )
    result = verifier.verify_pair(vrepo, "main", _pair())
    # The only test that could confirm (B's) was wrong on B: repair round.
    assert len(prompts) == 2
    assert "did not pass on its own branch" in prompts[1]
    assert "Your previous test" in prompts[1]
    assert result.attempts == 2
    assert result.status == "confirmed"


def test_suspected_when_test_fails_on_own_branch_after_all_attempts(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([BOTH_WRONG], prompts))
    result = verifier.verify_pair(vrepo, "main", _pair())
    assert result.status == "suspected"
    assert "does not pass on branch A and B" in result.reason
    assert result.attempts == settings.verify_max_attempts


def test_suspected_when_merge_also_passes(vrepo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([WEAK_TEST], prompts))
    result = verifier.verify_pair(vrepo, "main", _pair())
    assert result.status == "suspected"
    assert result.reason == "generated test also passes on the merge"
    assert result.runs[-1].failed == 0
    # One sharpening round was attempted with the merge result as feedback.
    assert len(prompts) == settings.verify_max_attempts == 4
    assert "does not exercise the interaction" in prompts[1]
    # Names the tests that pin unchanged base behaviour.
    assert "pin unchanged behaviour instead of their branch's change: " in prompts[1]


def test_sharpen_round_turns_a_weak_test_into_a_confirmation(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(
        "app.services.nemotron.complete", _scripted([WEAK_TEST, GOOD_TEST], prompts)
    )
    result = verifier.verify_pair(vrepo, "main", _pair())
    assert result.status == "confirmed"
    assert result.attempts == 2
    assert result.test_code == GOOD_TEST


def test_unsafe_generated_code_is_rejected_before_running(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = "import subprocess\n\n\ndef test_branch_a_x():\n    pass\n\n\ndef test_branch_b_x():\n    pass\n"
    prompts: list[str] = []
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([bad], prompts))
    result = verifier.verify_pair(vrepo, "main", _pair())
    assert result.status == "error"
    assert "subprocess" in result.reason
    assert result.runs == []


@pytest.mark.parametrize(
    "code, fragment",
    [
        ("def test_branch_a_x():\n    pass\n", "test_branch_b"),
        ("def test_branch_a(:\n", "not valid Python"),
        ("import os\n\ndef test_branch_a_x():\n    os.system('x')\n\ndef test_branch_b_y():\n    pass\n", ".system"),
        ("def test_branch_a_x():\n    open('f', 'w')\n\ndef test_branch_b_y():\n    pass\n", "open()"),
    ],
)
def test_validate_code(code: str, fragment: str) -> None:
    problem = verifier.validate_code(code)
    assert problem is not None and fragment in problem
    assert verifier.validate_code(GOOD_TEST) is None


def test_extract_code_prefers_fenced_block() -> None:
    text = "<think>```python\nnope\n```</think>\nHere:\n```python\nimport calc\n```\n"
    assert verifier.extract_code(text) == "import calc\n"


def _raw_run(tmp_path: Path, body: str, timeout: float = 20.0):
    work = tmp_path / "work"
    work.mkdir()
    (work / verifier.TEST_FILENAME).write_text(body)
    return verifier.run_test(work, tmp_path, target="a", ref="x", selection="branch", timeout=timeout)


def test_subprocess_env_is_stripped_of_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", "super-secret-value")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "another-secret")
    body = (
        "import os\n\n\ndef test_branch_env():\n"
        "    assert 'NEBIUS_API_KEY' not in os.environ\n"
        "    assert 'AWS_SECRET_ACCESS_KEY' not in os.environ\n"
        "    assert not any('secret' in v for v in os.environ.values())\n"
    )
    run = _raw_run(tmp_path, body)
    assert run.exit_code == 0 and run.passed == 1, run.output
    assert "super-secret-value" not in run.output
    env = verifier.clean_env(tmp_path)
    assert "NEBIUS_API_KEY" not in env and set(env) >= {"PATH", "HOME"}


def test_subprocess_has_no_network(tmp_path: Path) -> None:
    body = (
        "import socket\nimport pytest\n\n\ndef test_branch_net():\n"
        "    with pytest.raises(OSError):\n"
        "        socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
    )
    run = _raw_run(tmp_path, body)
    assert run.exit_code == 0 and run.passed == 1, run.output


def test_subprocess_timeout_is_enforced(tmp_path: Path) -> None:
    body = "def test_branch_forever():\n    while True:\n        pass\n"
    run = _raw_run(tmp_path, body, timeout=2.0)
    assert run.timed_out is True
    assert run.exit_code is None
    assert run.duration_s < 15


def test_same_size_rewrite_in_the_same_second_runs_the_new_source(tmp_path: Path) -> None:
    """Pytest's rewritten pyc is trusted when mtime (whole seconds) and size match."""
    import os

    body_fail = "def test_branch_a_x():\n    assert 1 == 2\n"
    body_pass = "def test_branch_a_x():\n    assert 1 == 1\n"
    assert len(body_fail) == len(body_pass)
    work = tmp_path / "work"
    work.mkdir()
    path = work / verifier.TEST_FILENAME
    path.write_text(body_fail)
    failed = verifier.run_test(work, tmp_path, target="a", ref="x", selection="branch_a", timeout=20)
    assert failed.failed == 1 and failed.passed == 0, failed.output
    stamped = path.stat()
    path.write_text(body_pass)
    os.utime(path, (stamped.st_atime, stamped.st_mtime))
    passed = verifier.run_test(work, tmp_path, target="a", ref="x", selection="branch_a", timeout=20)
    assert passed.passed == 1 and passed.failed == 0, passed.output


def test_eligibility_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert verifier.is_high_risk(_pair()) is True  # model level high
    assert verifier.is_high_risk(_pair(model_level="low", model_score=20, score=15, level="low")) is False
    assert verifier.is_high_risk(_pair(score_source="git")) is False
    monkeypatch.setattr(settings, "verify_allowed_roots", "")
    assert verifier.repo_allowed(verifier.DEMO_ROOT / "checkout-demo") is True
    assert verifier.repo_allowed(tmp_path) is False


def _route_by_system_prompt(risk_score: float, test_code: str, calls: dict):
    def fake_complete(prompt: str, **kwargs) -> str:
        if kwargs.get("system_prompt") == verifier.SYSTEM_PROMPT:
            calls["verify"] = calls.get("verify", 0) + 1
            return LLMText(f"```python\n{test_code}```", 900, 300)
        calls["risk"] = calls.get("risk", 0) + 1
        body = {"risk_score": risk_score, "risk_level": "high", "rationale": "scale vs total."}
        return LLMText(json.dumps(body), 1000, 100)

    return fake_complete


def test_analyze_verifies_only_high_risk_pairs_and_never_docs(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict = {}
    monkeypatch.setattr(
        "app.services.nemotron.complete", _route_by_system_prompt(90, GOOD_TEST, calls)
    )
    branches = ["feat/scale", "feat/total", "docs/readme"]
    res = sentinel.analyze(vrepo, "main", branches, verify=True, verify_allowed=True)
    by_pair = {frozenset((p.branch_a, p.branch_b)): p for p in res.pairs}
    hot = by_pair[frozenset(("feat/scale", "feat/total"))]
    assert hot.verification is not None and hot.verification.status == "confirmed"
    # Confirmed is authoritative: high, floored at 90; git/model scores untouched.
    assert hot.level == "high"
    assert hot.score == 90.0
    assert hot.score_note == "raised: confirmed by test"
    assert hot.model_score == 90 and hot.git_score is not None and hot.git_score < 90
    assert hot.blended_score is not None and hot.blended_score < 90
    assert res.pairs[0] is hot or res.pairs[0].branch_b == hot.branch_b
    for other in ("feat/scale", "feat/total"):
        docs = by_pair[frozenset(("docs/readme", other))]
        assert docs.fallback_reason == "prefiltered"
        assert docs.verification is None
    assert calls == {"risk": 1, "verify": 1}
    assert res.usage is not None
    assert res.usage.llm_calls == 2
    assert res.usage.prompt_tokens == 1900 and res.usage.completion_tokens == 400
    assert res.usage.verify_pairs == 1 and res.usage.verify_confirmed == 1

    # Identical second run: zero LLM calls (risk + test both cached), same verdict.
    again = sentinel.analyze(vrepo, "main", branches, verify=True, verify_allowed=True)
    assert calls == {"risk": 1, "verify": 1}
    assert again.usage is not None and again.usage.llm_calls == 0
    hot2 = next(p for p in again.pairs if p.verification)
    assert hot2.verification.status == "confirmed" and hot2.verification.cached_test


def test_analyze_skips_execution_when_not_allowed(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict = {}
    monkeypatch.setattr(
        "app.services.nemotron.complete", _route_by_system_prompt(90, GOOD_TEST, calls)
    )
    res = sentinel.analyze(
        vrepo, "main", ["feat/scale", "feat/total"], verify=True, verify_allowed=False
    )
    hot = res.pairs[0]
    assert hot.verification is not None and hot.verification.status == "skipped"
    assert "verify" not in calls


def test_api_only_verifies_local_repos_under_allowed_roots(
    vrepo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    calls: dict = {}
    monkeypatch.setattr(
        "app.services.nemotron.complete", _route_by_system_prompt(90, GOOD_TEST, calls)
    )
    client = TestClient(app)
    body = client.post(
        "/api/analyze",
        json={"repo_path": str(vrepo), "branches": ["feat/scale", "feat/total"]},
    ).json()
    verification = body["pairs"][0]["verification"]
    assert verification["status"] == "confirmed"
    assert verification["test_code"] == GOOD_TEST
    assert [r["target"] for r in verification["runs"]] == ["a", "b", "base", "merge"]

    monkeypatch.setattr(settings, "verify_allowed_roots", str(tmp_path / "elsewhere"))
    body = client.post(
        "/api/analyze",
        json={"repo_path": str(vrepo), "branches": ["feat/scale", "feat/total"], "use_cache": False},
    ).json()
    assert body["pairs"][0]["verification"]["status"] == "skipped"


PINS_BASE = '''import calc


def test_branch_a_scale_triples():
    assert calc.scale(2) == 6


def test_branch_b_scale_unchanged():
    # docs branch: code is the base code, scale(2) == 4
    assert calc.scale(2) == 4
'''


def test_docs_branch_cannot_be_confirmed_by_pinning_base_behaviour(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A docs-only branch has no change of its own; a 'B intent' test that just
    pins base behaviour fails on the merge (A changed it on purpose) but must
    not count as a confirmed conflict."""
    prompts: list[str] = []
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([PINS_BASE], prompts))
    result = verifier.verify_pair(vrepo, "main", _pair("feat/scale", "docs/readme"))
    merge = result.runs[-1]
    assert merge.cases["test_branch_b_scale_unchanged"] == "failed"
    assert result.status == "suspected"
    assert result.reason == "merge only fails tests that pin unchanged base behaviour"
    assert result.confirming_tests == []


def test_empty_model_reply_is_retried_and_tokens_are_counted(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.nemotron import NemotronError

    calls: list[int] = []

    def flaky(prompt: str, **_kwargs) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise NemotronError("model_error", "empty model response (finish_reason=length)", 800, 8192)
        return LLMText(f"```python\n{GOOD_TEST}```", 900, 300)

    monkeypatch.setattr("app.services.nemotron.complete", flaky)
    meter = UsageMeter()
    result = verifier.verify_pair(vrepo, "main", _pair(), meter=meter)
    assert result.status == "confirmed"
    assert result.attempts == 2
    assert meter.llm_calls == 2
    assert (result.prompt_tokens, result.completion_tokens) == (1700, 8492)


def test_reasoning_overflow_retries_without_thinking_even_after_a_test_ran(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.nemotron import NemotronError

    seen: list[dict | None] = []

    def fake(prompt: str, **kwargs) -> str:
        seen.append(kwargs.get("extra_body"))
        if len(seen) == 1:
            return LLMText(f"```python\n{BOTH_WRONG}```", 900, 300)
        if len(seen) == 2:
            raise NemotronError("model_error", "empty model response (finish_reason=length)", 2000, 16384)
        return LLMText(f"```python\n{GOOD_TEST}```", 900, 300)

    monkeypatch.setattr("app.services.nemotron.complete", fake)
    result = verifier.verify_pair(vrepo, "main", _pair())
    assert result.status == "confirmed"
    assert result.attempts == 3
    assert seen[0] is None and seen[1] is None
    assert seen[2] == verifier.NO_THINKING


def test_stale_worktrees_from_a_killed_run_are_cleaned_up(vrepo: Path, tmp_path: Path) -> None:
    import os
    import tempfile

    stale_root = Path(tempfile.mkdtemp(prefix="mg-verify-"))
    _git(vrepo, "worktree", "add", "--detach", "--quiet", str(stale_root / "a"), "feat/scale")
    fresh_root = Path(tempfile.mkdtemp(prefix="mg-verify-"))
    _git(vrepo, "worktree", "add", "--detach", "--quiet", str(fresh_root / "a"), "feat/total")
    old = 2 * 60 * 60
    os.utime(stale_root, (os.path.getatime(stale_root) - old, os.path.getmtime(stale_root) - old))
    assert len(_worktrees(vrepo)) == 3

    assert verifier.cleanup_stale_worktrees(vrepo) == 1
    remaining = "\n".join(_worktrees(vrepo))
    assert str(stale_root.name) not in remaining
    assert str(fresh_root.name) in remaining  # a concurrent run is left alone
    assert not stale_root.exists()
    _git(vrepo, "worktree", "remove", "--force", str(fresh_root / "a"))


def test_confirmed_verdict_is_authoritative_and_suspected_keeps_the_blend() -> None:
    from app.schemas import Verification

    blended = _pair(score=47.5, level="medium")
    confirmed = sentinel.apply_verification(blended, Verification(status="confirmed", reason="r"))
    assert (confirmed.score, confirmed.level) == (90.0, "high")
    assert confirmed.blended_score == 47.5
    assert confirmed.score_note == "raised: confirmed by test"
    assert (confirmed.git_score, confirmed.model_score, confirmed.model_level) == (10, 85, "high")

    above = sentinel.apply_verification(
        _pair(score=96.0, level="high"), Verification(status="confirmed", reason="r")
    )
    assert above.score == 96.0  # a floor, not a cap

    for status in ("suspected", "error", "skipped"):
        kept = sentinel.apply_verification(blended, Verification(status=status, reason="r"))
        assert (kept.score, kept.level) == (47.5, "medium")
        assert kept.score_note is None and kept.blended_score is None


def test_project_venv_editable_installs_point_at_the_worktree(tmp_path: Path) -> None:
    """A repo's own .venv is used, and its editable install of the main checkout
    is redirected to the worktree under test."""
    import sysconfig

    src = tmp_path / "src"
    (src / "lib").mkdir(parents=True)
    (src / "lib" / "mod.py").write_text('VALUE = "main checkout"\n')
    work = tmp_path / "work"
    (work / "lib").mkdir(parents=True)
    (work / "lib" / "mod.py").write_text('VALUE = "worktree"\n')
    venv = src / ".venv"  # like `uv sync`: the venv lives inside the repo
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    site = next(venv.glob("lib/python3*/site-packages"))
    # pytest from the backend env, plus an "editable install" of src/lib.
    (site / "zz_editable.pth").write_text(
        f"{sysconfig.get_paths()['purelib']}\n{src / 'lib'}\n"
    )
    (work / verifier.TEST_FILENAME).write_text(
        "import os\nimport sys\n\nimport mod\n\n\ndef test_branch_a_uses_worktree():\n"
        "    assert mod.VALUE == 'worktree'\n"
        "    # the venv's own site-packages must not be redirected\n"
        "    assert all(os.path.isdir(p) for p in sys.path if p.endswith('site-packages'))\n"
    )
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    python = str(venv / "bin" / "python")
    run = verifier.run_test(work, tmp, target="a", ref="x", selection="branch", timeout=30,
                            python=python, src_root=src)
    assert run.exit_code == 0 and run.passed == 1, run.output
    # Without the redirect the main checkout's code would be under test.
    (tmp / "report-a.xml").unlink(missing_ok=True)
    stale = verifier.run_test(work, tmp, target="a", ref="x", selection="branch", timeout=30,
                              python=python)
    assert stale.failed == 1, stale.output


def test_project_python_requires_allowed_repo_with_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    py = repo / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    assert verifier.project_python(repo) == sys.executable  # not under an allowed root
    monkeypatch.setattr(verifier.settings, "verify_allowed_roots", str(tmp_path))
    assert verifier.project_python(repo) == sys.executable  # no pytest in the venv
    (repo / ".venv" / "lib" / "python3.12" / "site-packages" / "pytest").mkdir(parents=True)
    assert verifier.project_python(repo) == str(py)


def test_focus_paths_from_evidence() -> None:
    evidence = [
        "Preview: changed by a (pkg/core/models.py), called by b (tests/test_ui.py)",
        "Other: changed by both a and b (pkg/core/models.py)",
    ]
    assert verifier.focus_paths(evidence) == ["pkg/core/models.py", "tests/test_ui.py"]


def test_collection_error_detection_and_prompt_guidance(tmp_path: Path) -> None:
    run = _raw_run(tmp_path, "from not_on_this_branch import thing\n\n\ndef test_branch_b_x():\n    pass\n")
    assert run.errors == 1 and verifier._collection_error(run), run.output
    assert "inside that branch's test functions" in verifier.SYSTEM_PROMPT


def test_confirming_test_counts_even_if_another_generated_test_is_broken(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([WRONG_ON_A], prompts))
    result = verifier.verify_pair(vrepo, "main", _pair())
    # A's test is wrong on A (a broken test, discarded); B's test passes on B,
    # fails on the base and fails on the merge: that is the evidence.
    assert result.status == "confirmed", result.reason
    assert result.confirming_tests == ["test_branch_b_total_adds_ten"]
    assert "1 generated test(s) that did not pass on their own branch were discarded" in result.reason
    assert len(prompts) == 1


def test_static_break_makes_a_pair_eligible_for_verify() -> None:
    low = _pair(score_source="git", model_score=None, model_level=None, score=5.0, level="low")
    assert not verifier.is_high_risk(low)
    assert verifier.is_high_risk(low.model_copy(update={"static_breaks": ["f: call site ..."]}))


def test_break_hints_point_the_test_at_the_broken_call_site(tmp_path: Path) -> None:
    r = tmp_path / "bh"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "models.py").write_text("class Preview:\n    a: int\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    _git(r, "checkout", "-q", "-b", "fields")
    (r / "models.py").write_text("class Preview:\n    a: int\n    b: int\n")
    _git(r, "commit", "-qam", "required b")
    _git(r, "checkout", "-q", "main")
    _git(r, "checkout", "-q", "-b", "caller")
    (r / "use.py").write_text("from models import Preview\n\n\ndef make():\n    return Preview(a=1)\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "caller")
    evidence = ["Preview: changed by fields (models.py), called by caller (use.py)"]
    hints = verifier.break_hints(r, "main", "fields", "caller", evidence)
    assert hints == [
        "- Preview: call site use.py:5 on caller does not pass new required b. Branch B "
        "(caller) calls Preview at use.py:5 inside make(). A test_branch_b_* that runs that "
        "code path (import it from use.py) passes on branch B, fails on the merge-base (where "
        "that code does not exist yet) and fails on the merge."
    ]
    prompt = verifier.build_test_prompt(r, "main", "fields", "caller", evidence=evidence)
    assert "== Exact static check: call sites that no longer match ==" in prompt


# Verbatim (trimmed docstrings) from a live forced-verify run on two harmless
# branches of demo/harmless-demo: it "confirmed" by inspecting code and by
# expecting the other branch's function to be missing.
GAMED_LIVE = '''import inspect
from geometry import perimeter


def test_branch_a_summary_no_half_in_perimeter():
    try:
        from report import summary
    except ImportError:
        assert False, "summary() missing (branch A's change)"
    src = inspect.getsource(perimeter)
    assert 'half' not in src, (
        f"perimeter() should not contain 'half', but got:\\n{src}"
    )
    assert summary(2, 3) == {"area": 6, "perimeter": 10}


def test_branch_b_perimeter_has_half_and_no_summary():
    src = inspect.getsource(perimeter)
    assert 'half' in src
    try:
        from report import summary
        assert False, "summary() should not be present (branch B's intent)"
    except ImportError:
        pass


def test_branch_a_scale_and_perimeter_not_refactored():
    assert 'half' not in perimeter.__code__.co_varnames


def test_branch_b_behaviour_only():
    assert perimeter(2, 3) == 10
'''

GAMED_CALC = '''import inspect
import calc


def test_branch_a_scale_source():
    # branch A changed the multiplier; B's change must not be there
    src = inspect.getsource(calc)
    assert "x * 3" in src
    assert "+ 10" not in src


def test_branch_b_total_consts():
    assert 10 in calc.total.__code__.co_consts
    assert not hasattr(calc, "never_defined") or "x * 3" not in inspect.getsource(calc)
    assert "x * 3" not in inspect.getsource(calc.scale)
'''


def test_behaviour_problems_flag_code_inspection_and_absence_checks() -> None:
    found = verifier.behaviour_problems(GAMED_LIVE)
    assert set(found) == {
        "test_branch_a_summary_no_half_in_perimeter",
        "test_branch_b_perimeter_has_half_and_no_summary",
        "test_branch_a_scale_and_perimeter_not_refactored",
    }
    assert "passes when a name is missing (except ImportError)" in found[
        "test_branch_b_perimeter_has_half_and_no_summary"]
    assert found["test_branch_a_scale_and_perimeter_not_refactored"] == [
        "inspects code (.__code__)", "inspects code (.co_varnames)"]
    lines = verifier.shape_lines(GAMED_LIVE)
    code_lines = GAMED_LIVE.splitlines()
    a_lines = {code_lines[i - 1].strip() for i in lines["test_branch_a_summary_no_half_in_perimeter"]}
    # The value derived from inspect.getsource is tainted; the behaviour check is not.
    assert "assert 'half' not in src, (" in a_lines
    assert 'assert summary(2, 3) == {"area": 6, "perimeter": 10}' not in a_lines
    # A try that fails when the name exists does not "tolerate" absence here
    # (assert False in the handler), so it is not flagged.
    assert 'assert False, "summary() missing (branch A\'s change)"' not in a_lines


def test_shape_failures_never_confirm_but_behaviour_failures_still_do(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([GAMED_CALC], prompts))
    result = verifier.verify_pair(vrepo, "main", _pair(), use_cache=False)
    run_m = result.runs[-1]
    assert run_m.failed == 2 and set(run_m.failure_lines) == set(run_m.cases)
    assert result.status == "suspected", result.reason
    assert result.confirming_tests == []
    assert result.reason.startswith("merge only fails tests that inspect code")
    assert "check code shape or absence instead of behaviour" in prompts[1]

    # The same hasattr() noise in an otherwise behavioural test: the merge
    # fails on the behaviour line, so it still confirms.
    noisy = GOOD_TEST.replace(
        "    assert calc.total(1) == 12",
        "    assert hasattr(calc, 'total')\n    assert calc.total(1) == 12",
    )
    prompts.clear()
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([noisy], prompts))
    result = verifier.verify_pair(vrepo, "main", _pair(), use_cache=False)
    assert result.status == "confirmed", result.reason
    assert result.confirming_tests == ["test_branch_b_total_adds_ten"]


def _intent(import_line: str) -> str:
    return (
        f"{import_line}\n\n\n"
        "def test_branch_a_x():\n"
        "    pass\n\n\n"
        "def test_branch_b_y():\n"
        "    pass\n"
    )


def test_requirements_txt_declares_network_clients(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "\n".join([
            "# runtime",
            "Requests[security]>=2.32",
            "httpx[http2]>=0.27 ; python_version >= '3.11'",
            "aiohttp @ https://example.com/aiohttp.tar.gz#sha256=abc",
            "URLLib3==2.2.0",
            "requests-mock>=1",  # not the requests distribution
            "pydantic>=2",
            "-r other.txt",
            "-e .",
            "",
        ])
    )
    # A requirements file that is not at the root (and not requirements/*.txt)
    # is not the repo's declared dependency list.
    nested = tmp_path / "pkg"
    nested.mkdir()
    (nested / "requirements.txt").write_text("httpx\n")
    assert verifier.declared_network_modules(tmp_path) == frozenset({
        "requests", "httpx", "aiohttp", "urllib3",
    })


def test_requirements_variants_and_requirements_directory(tmp_path: Path) -> None:
    (tmp_path / "requirements-dev.txt").write_text("httpx\n")
    (tmp_path / "requirements_test.txt").write_text("aiohttp\n")
    reqs = tmp_path / "requirements"
    reqs.mkdir()
    (reqs / "prod.txt").write_text("urllib3\nrequests\n")
    (reqs / "notes.md").write_text("requests\n")  # not a txt manifest
    deeper = reqs / "nested"
    deeper.mkdir()
    (deeper / "extra.txt").write_text("httpx\n")  # requirements/*/*.txt is out of scope
    assert verifier.declared_network_modules(tmp_path) == frozenset({
        "requests", "httpx", "aiohttp", "urllib3",
    })


def test_pyproject_project_and_optional_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        "name = 'demo'\n"
        "version = '0.1.0'\n"
        "dependencies = [\n"
        "  'Requests>=2',\n"
        "  'pydantic>=2',\n"
        "]\n"
        "\n"
        "[project.optional-dependencies]\n"
        "dev = ['HTTPX>=0.27']\n"
        "cli = ['AIOHTTP>=3']\n"
        "broken = 'urllib3'\n"  # not a list: ignored, not guessed
    )
    assert verifier.declared_network_modules(tmp_path) == frozenset({
        "requests", "httpx", "aiohttp",
    })


def test_pyproject_poetry_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry.dependencies]\n"
        "python = '^3.11'\n"
        "requests = '^2.31'\n"
        "urllib3 = {version = '^2', optional = true}\n"
        "httpx = {version = '>=0.27', extras = ['http2']}\n"
        "\n"
        "[tool.poetry.group.dev.dependencies]\n"
        "aiohttp = '*'\n"  # group tables are not [tool.poetry.dependencies]
    )
    assert verifier.declared_network_modules(tmp_path) == frozenset({
        "requests", "urllib3", "httpx",
    })


def test_setup_cfg_install_requires(tmp_path: Path) -> None:
    (tmp_path / "setup.cfg").write_text(
        "[metadata]\n"
        "name = demo\n"
        "\n"
        "[options]\n"
        "install_requires =\n"
        "    requests>=2.31\n"
        "    httpx[http2]>=0.27\n"
        "    pydantic\n"
        "\n"
        "[options.extras_require]\n"
        "dev =\n"
        "    aiohttp\n"
    )
    assert verifier.declared_network_modules(tmp_path) == frozenset({"requests", "httpx"})


def test_pipfile_packages(tmp_path: Path) -> None:
    (tmp_path / "Pipfile").write_text(
        "[packages]\n"
        "requests = '*'\n"
        "aiohttp = {extras = ['speedups'], version = '>=3'}\n"
        "urllib3 = '==2.2.0'\n"
        "\n"
        "[dev-packages]\n"
        "httpx = '*'\n"
    )
    assert verifier.declared_network_modules(tmp_path) == frozenset({
        "requests", "aiohttp", "urllib3",
    })


def test_setup_py_install_requires_strings_are_scanned_not_executed(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text(
        "raise SystemExit('setup.py was executed')\n"
        "\n"
        "# install_requires=['httpx']\n"
        "setup(\n"
        "    name='demo',\n"
        "    install_requires=[\n"
        "        'requests[security]>=2.31',\n"
        "        \"aiohttp>=3\",\n"
        "    ],\n"
        ")\n"
    )
    assert verifier.declared_network_modules(tmp_path) == frozenset({"requests", "aiohttp"})
    # A name that is not a literal in install_requires is not executed or guessed.
    (tmp_path / "setup.py").write_text(
        "REQUIRES = ['urllib3']\n"
        "setup(install_requires=REQUIRES)\n"
    )
    assert verifier.declared_network_modules(tmp_path) == frozenset()


def test_unparseable_manifests_contribute_nothing(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("this = [\n")
    (tmp_path / "setup.cfg").write_text("[options\ninstall_requires = requests\n")
    (tmp_path / "Pipfile").write_text("[packages\nrequests = '*'\n")
    assert verifier.declared_network_modules(tmp_path) == frozenset()
    # A sibling that does parse still counts. The broken files add nothing.
    (tmp_path / "requirements.txt").write_text("httpx\n")
    assert verifier.declared_network_modules(tmp_path) == frozenset({"httpx"})


def test_manifest_outside_the_tree_and_oversize_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("requests\n")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "requirements.txt").symlink_to(outside)
    assert verifier.declared_network_modules(root) == frozenset()

    monkeypatch.setattr(verifier, "_MAX_MANIFEST_CHARS", 16)
    (root / "requirements.txt").unlink()
    (root / "requirements.txt").write_text("requests\n" + ("x" * 32))
    assert verifier.declared_network_modules(root) == frozenset()
    # A missing git ref fails closed too.
    assert verifier.declared_network_modules(root, "HEAD") == frozenset()


def test_declared_modules_follow_the_git_ref_not_the_dirty_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "requirements.txt").write_text("httpx\n")
    (repo / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='0'\ndependencies=['aiohttp']\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "deps")
    (repo / "requirements.txt").write_text("requests\n")  # dirty worktree
    assert verifier.declared_network_modules(repo, "HEAD") == frozenset({"httpx", "aiohttp"})
    assert verifier.declared_network_modules(repo) == frozenset({"requests", "aiohttp"})


def test_network_clients_allowed_only_when_declared() -> None:
    requests_test = _intent("import requests")
    adapters = _intent("from requests.adapters import HTTPAdapter")
    httpx_test = _intent("from httpx import Client")
    allowed = frozenset({"requests", "httpx"})
    assert verifier.validate_code(requests_test) is not None
    assert "forbidden module requests" in (verifier.validate_code(requests_test) or "")
    assert verifier.validate_code(requests_test, allowed) is None
    assert verifier.validate_code(adapters, allowed) is None
    assert verifier.validate_code(httpx_test, allowed) is None
    assert "forbidden module requests" in (verifier.validate_code(adapters, frozenset()) or "")
    # Declaring one client does not open the others.
    only_requests = frozenset({"requests"})
    assert "forbidden module httpx" in (verifier.validate_code(httpx_test, only_requests) or "")


def test_low_level_modules_stay_forbidden_even_if_named() -> None:
    for module in ("socket", "subprocess", "urllib", "urllib.request", "http.client",
                   "ssl", "asyncio", "shutil", "ctypes"):
        code = _intent(f"import {module}")
        problem = verifier.validate_code(code, frozenset({
            "socket", "subprocess", "urllib", "http", "ssl", "asyncio", "shutil", "ctypes",
            "requests",
        }))
        assert problem is not None and module.split(".")[0] in problem, module


def test_manifest_cannot_declare_socket_or_subprocess(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("socket\nsubprocess\nrequests\nshutil\n")
    assert verifier.declared_network_modules(tmp_path) == frozenset({"requests"})


def test_verify_passes_the_repos_declared_deps(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branch declares requests; main's checkout does not. The call site
    still passes that set into validate_code, and the test is allowed to run."""
    _git(vrepo, "checkout", "-q", "feat/scale")
    (vrepo / "requirements.txt").write_text("requests\npydantic\nsocket\nsubprocess\n")
    _git(vrepo, "add", "requirements.txt")
    _git(vrepo, "commit", "-qm", "declare requests")
    _git(vrepo, "checkout", "-q", "main")
    assert not (vrepo / "requirements.txt").exists()

    seen: list[frozenset[str]] = []
    real = verifier.validate_code

    def spy(code: str, allowed_modules: frozenset[str] = frozenset()) -> str | None:
        seen.append(frozenset(allowed_modules))
        return real(code, allowed_modules)

    monkeypatch.setattr(verifier, "validate_code", spy)
    monkeypatch.setattr(settings, "verify_max_attempts", 1)
    code = GOOD_TEST.replace("import calc\n", "import calc\nimport requests\n", 1)
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([code], []))
    result = verifier.verify_pair(vrepo, "main", _pair(), use_cache=False)

    assert seen == [frozenset({"requests"})]
    assert result.status == "confirmed", result.reason
    assert result.runs[0].passed == 1, result.runs[0].output


def test_verify_still_rejects_undeclared_requests(
    vrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "verify_max_attempts", 1)
    code = GOOD_TEST.replace("import calc\n", "import calc\nimport requests\n", 1)
    monkeypatch.setattr("app.services.nemotron.complete", _scripted([code], []))
    result = verifier.verify_pair(vrepo, "main", _pair(), use_cache=False)
    assert result.status == "error"
    assert result.reason == "generated test imports forbidden module requests"
    assert result.runs == []


def test_requests_get_is_blocked_by_the_socket_guard(tmp_path: Path) -> None:
    """requests is importable here, and connect/getaddrinfo are patched, so
    the call fails in-process. No mock and no real network."""
    body = (
        "import requests\n\n\n"
        "def test_branch_net():\n"
        "    try:\n"
        "        requests.get('http://example.com', timeout=2)\n"
        "    except Exception as exc:\n"
        "        text = str(exc)\n"
        "        cause = exc.__cause__\n"
        "        seen = set()\n"
        "        while cause is not None and id(cause) not in seen:\n"
        "            seen.add(id(cause))\n"
        "            text += ' ' + str(cause)\n"
        "            cause = cause.__cause__\n"
        "        assert 'network disabled by the MergeGuard verify sandbox' in text, text\n"
        "    else:\n"
        "        raise AssertionError('request was not blocked')\n"
    )
    run = _raw_run(tmp_path, body)
    assert run.exit_code == 0 and run.passed == 1, run.output
