"""Branch tests on the merge (no LLM): real git worktrees and real pytest runs."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.schemas import BranchTestRun
from app.services import branch_tests, sentinel

FILLER = "".join(f"# filler line {i}\n" for i in range(12))
STORE = '''def read_floor():
    return {"proven": 3}


''' + FILLER
STORE_TUPLE = STORE.replace('return {"proven": 3}', 'return {"proven": 3}, []')
TEST_STORE = '''import store


def test_read_floor():
    assert store.read_floor()["proven"] == 3
'''
TEST_STORE_TUPLE = '''import store


def test_read_floor():
    floor, problems = store.read_floor()
    assert floor["proven"] == 3 and problems == []
'''
REPORT = '''import store


def proven():
    return store.read_floor().get("proven", 0)
'''
TEST_REPORT = '''import report


def test_proven_count():
    assert report.proven() == 3
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _branch(repo: Path, name: str, files: dict[str, str | None], msg: str) -> None:
    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", name)
    for path, text in files.items():
        target = repo / path
        if text is None:
            _git(repo, "rm", "-q", path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    _git(repo, "checkout", "-q", "main")


@pytest.fixture()
def brepo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "allowed" / "floor-repo"
    r.mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "store.py").write_text(STORE)
    (r / "test_store.py").write_text(TEST_STORE)
    (r / "a_part.py").write_text("BASE = 1\n")
    (r / "b_part.py").write_text("DELTA = 0\n")
    (r / "config.py").write_text("from a_part import BASE\nfrom b_part import DELTA\n\nVALUE = BASE + DELTA\n")
    (r / "test_value.py").write_text("import config\n\n\ndef test_value():\n    assert config.VALUE == 2\n")
    (r / "README.md").write_text("# floor\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    # The KISS shape: one branch changes the return value and fixes the callers
    # it knows about; the other adds a new caller written against the old shape.
    _branch(r, "feat/tuple", {"store.py": STORE_TUPLE, "test_store.py": TEST_STORE_TUPLE},
            "read_floor returns (floor, problems)")
    _branch(r, "feat/report", {"report.py": REPORT, "test_report.py": TEST_REPORT},
            "report proven count")
    # Harmless: same file as feat/tuple (far away) plus its own test.
    _branch(r, "feat/describe", {
        "store.py": STORE + 'def describe():\n    return "store"\n',
        "test_describe.py": "import store\n\n\ndef test_describe():\n    assert store.describe() == 'store'\n",
    }, "describe")
    # Pre-existing failure: test_value fails on main (1 != 2).
    _branch(r, "feat/base-two", {"a_part.py": "BASE = 2\n",
                                 "test_value.py": "import config\n\n\ndef test_value():\n    # fixed by BASE=2\n    assert config.VALUE == 2\n"},
            "base two")
    _branch(r, "feat/delta", {"b_part.py": "DELTA = -1\n"}, "delta")
    # Rename vs a new importer: the merge fails to import the new test module.
    _branch(r, "feat/rename", {"store.py": None, "storage.py": STORE,
                               "test_store.py": TEST_STORE.replace("import store", "import storage as store")},
            "rename store -> storage")
    _branch(r, "feat/ts", {"web/app.test.ts": "test('x', () => {});\n"}, "ts test")
    _branch(r, "docs/readme", {"README.md": "# floor\n\nDocs.\n"}, "docs")
    monkeypatch.setattr(settings, "verify_allowed_roots", str(tmp_path / "allowed"))
    return r


def _worktrees(repo: Path) -> int:
    return sum(1 for ln in _git(repo, "worktree", "list", "--porcelain").splitlines()
               if ln.startswith("worktree "))


def test_new_caller_against_changed_return_value_is_confirmed(brepo: Path) -> None:
    res = branch_tests.check_pair(brepo, "main", "feat/tuple", "feat/report")
    assert res.status == "confirmed", res.reason
    assert res.files_a == ["test_store.py"] and res.files_b == ["test_report.py"]
    [hit] = res.confirming
    assert hit.test_id == "test_report::test_proven_count"
    assert hit.branch == "feat/report" and hit.file == "test_report.py"
    assert "tuple" in hit.excerpt and "get" in hit.excerpt
    assert [r.target for r in res.runs] == ["a", "b", "merge", "base", "merge-rerun"]
    assert _worktrees(brepo) == 1  # throwaway worktrees are gone


def test_harmless_pair_is_not_confirmed(brepo: Path) -> None:
    res = branch_tests.check_pair(brepo, "main", "feat/tuple", "feat/describe")
    assert res.status == "passed", res.reason
    assert res.confirming == []
    assert [r.target for r in res.runs] == ["a", "b", "merge"]  # no candidates: no extra runs
    assert res.runs[-1].failed == 0 and res.runs[-1].passed == 2


def test_test_already_failing_on_the_merge_base_does_not_confirm(brepo: Path) -> None:
    res = branch_tests.check_pair(brepo, "main", "feat/base-two", "feat/delta")
    merge = next(r for r in res.runs if r.target == "merge")
    assert merge.failed == 1  # it does fail on the merge ...
    assert res.status == "passed" and res.confirming == []  # ... but also on main


def test_module_that_no_longer_imports_on_the_merge_is_confirmed(brepo: Path) -> None:
    res = branch_tests.check_pair(brepo, "main", "feat/rename", "feat/report")
    assert res.status == "confirmed", res.reason
    assert [c.test_id for c in res.confirming] == ["test_report"]
    assert "store" in res.confirming[0].excerpt


def test_js_tests_without_a_runner_are_unsupported_and_no_tests_is_skipped(brepo: Path) -> None:
    res = branch_tests.check_pair(brepo, "main", "feat/ts", "docs/readme")
    assert res.status == "unsupported" and res.unsupported_files == ["web/app.test.ts"]
    assert res.runs == []
    res = branch_tests.check_pair(brepo, "main", "feat/delta", "docs/readme")
    assert res.status == "skipped"


def test_flaky_merge_failure_is_dropped(brepo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that fails once on the merge but passes on the re-run is not evidence."""
    calls: list[str] = []

    def fake_run(workdir, tmp_root, *, target, ref, files, python, src_root, timeout):
        calls.append(target)
        outcome = "failed" if target == "merge" else "passed"
        results = {"test_report::test_proven_count": (outcome, "boom")}
        if target in ("a", "base"):
            results = {}
        run = BranchTestRun(target=target, ref=ref, files=files, exit_code=0)
        return run, results

    monkeypatch.setattr(branch_tests, "run_files", fake_run)
    res = branch_tests.check_pair(brepo, "main", "feat/tuple", "feat/report")
    assert calls == ["a", "b", "merge", "base", "merge-rerun"]
    assert res.status == "passed" and res.confirming == []


def test_junit_ids_and_excerpts(tmp_path: Path) -> None:
    report = tmp_path / "r.xml"
    report.write_text(
        '<testsuites><testsuite tests="3">'
        '<testcase classname="pkg.test_a.TestX" name="test_one"/>'
        '<testcase classname="pkg.test_a" name="test_two"><failure message="assert 1 == 2">'
        "def test_two():\n&gt;   assert 1 == 2\nE   assert 1 == 2\nE    +  where 1 = f()"
        "</failure></testcase>"
        '<testcase classname="" name="pkg.test_b"><error message="collection failure">'
        "E   ModuleNotFoundError: No module named 'store'</error></testcase>"
        "</testsuite></testsuites>"
    )
    res = branch_tests.junit_results(report)
    assert res["pkg.test_a.TestX::test_one"] == ("passed", "")
    assert res["pkg.test_a::test_two"] == ("failed", "assert 1 == 2 | +  where 1 = f()")
    assert res["pkg.test_b"][0] == "error" and "No module named" in res["pkg.test_b"][1]
    files = ["pkg/test_a.py", "pkg/test_b.py"]
    assert branch_tests._file_of("pkg.test_a.TestX::test_one", files) == "pkg/test_a.py"
    assert branch_tests._file_of("pkg.test_b", files) == "pkg/test_b.py"


def test_analyze_confirms_by_branch_test_without_any_model(brepo: Path) -> None:
    """No API key: the model never runs, the branch tests still confirm."""
    branches = ["feat/tuple", "feat/report", "feat/describe"]
    res = sentinel.analyze(brepo, "main", branches, verify=True, verify_allowed=True)
    by_pair = {frozenset((p.branch_a, p.branch_b)): p for p in res.pairs}
    hot = by_pair[frozenset(("feat/tuple", "feat/report"))]
    assert hot.fallback_reason == "no_api_key" and hot.verification is None
    assert hot.branch_tests is not None and hot.branch_tests.status == "confirmed"
    assert hot.level == "high" and hot.score == 90.0
    assert hot.score_note == sentinel.BRANCH_TEST_NOTE
    harmless = by_pair[frozenset(("feat/tuple", "feat/describe"))]
    assert harmless.branch_tests is not None and harmless.branch_tests.status == "passed"
    assert harmless.level != "high" and harmless.score_note is None
    assert res.usage is not None
    assert res.usage.branch_test_confirmed == 1 and res.usage.llm_calls == 0
    assert res.pairs[0] is hot


def test_branch_tests_respect_allowed_roots_and_opt_out(
    brepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branches = ["feat/tuple", "feat/report"]
    res = sentinel.analyze(brepo, "main", branches, verify=True, verify_allowed=False)
    assert res.pairs[0].branch_tests is None
    monkeypatch.setattr(settings, "branch_tests_enabled", False)
    res = sentinel.analyze(brepo, "main", branches, verify=True, verify_allowed=True)
    assert res.pairs[0].branch_tests is None and res.pairs[0].level != "high"


def test_both_layers_confirming_says_so() -> None:
    from app.schemas import BranchTests, PairRisk, Verification

    pair = PairRisk(branch_a="a", branch_b="b", score=40, level="medium", shared_files=0,
                    overlapping_files=0, collisions=[], rationale="r")
    pair = sentinel.apply_verification(pair, Verification(status="confirmed"))
    pair = sentinel.apply_branch_tests(pair, BranchTests(status="confirmed"))
    assert pair.score == 90.0 and pair.blended_score == 40
    assert pair.score_note == sentinel.BOTH_NOTE


def test_stage_time_budget_skips_the_remaining_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas import BranchTests, PairRisk

    def mk(a: str, b: str) -> PairRisk:
        return PairRisk(branch_a=a, branch_b=b, score=0, level="none", shared_files=0,
                        overlapping_files=0, collisions=[], rationale="", prefilter_candidate=True)

    calls: list[tuple[str, str]] = []

    def fake(_repo, _base, a, b):
        calls.append((a, b))
        return BranchTests(status="passed")

    monkeypatch.setattr(branch_tests, "check_pair", fake)
    monkeypatch.setattr(settings, "branch_tests_total_seconds", 0.0)
    out = sentinel._run_branch_tests(Path("."), "main", [mk("a", "b"), mk("a", "c")])
    assert calls == []
    assert {r.status for r in out.values()} == {"skipped"}
    assert "time budget" in out[("a", "b")].reason
    monkeypatch.setattr(settings, "branch_tests_total_seconds", 600.0)
    out = sentinel._run_branch_tests(Path("."), "main", [mk("a", "b"), mk("a", "c")])
    assert len(calls) == 2 and {r.status for r in out.values()} == {"passed"}
