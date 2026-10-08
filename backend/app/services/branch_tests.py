"""Branch tests on the merge: a deterministic, LLM-free conflict check.

Each branch usually ships tests for its own change. A semantic merge conflict
very often shows up as one of THOSE tests failing once the other branch lands
(a fixture built for the old model, a caller written against the old return
value, a pinned output the other branch changed). So, for a pair (A, B) whose
textual merge is clean:

1. collect the test files each branch added or modified since the merge-base;
2. run A's on A, B's on B, and both sets on the merge of A+B (the existing
   verify sandbox: throwaway worktrees, the repo's ``.venv``, stripped
   environment, no network, timeout);
3. a test that passes on its own branch and fails on A+B is a candidate; the
   candidate files are re-run on the merge-base (a test already failing there
   is a pre-existing failure, not a conflict) and once more on the merge (a
   flaky test that passes the second time does not count).

A remaining test => ``confirmed`` (labelled "confirmed by branch test" so it is
never mistaken for the model's intent-test confirmation), with the test id and
a short error excerpt as evidence. No model call, so a low model score cannot
suppress it. Python/pytest only; JS/TS test files are reported as unsupported.
Only runs where verify-by-test may run (local repos under the allowed roots).
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from app.config import settings
from app.schemas import BranchTestFailure, BranchTestRun, BranchTests
from app.services import git_service, verifier

EXCERPT_CHARS = 300
_JS_TEST = re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs|mts|cts)$")
_STATUSES_FAILING = ("failed", "error")


def is_python_test(path: str) -> bool:
    name = Path(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def is_unsupported_test(path: str) -> bool:
    return bool(_JS_TEST.search(Path(path).name))


def changed_test_files(repo: Path, base_sha: str, sha: str) -> tuple[list[str], list[str]]:
    """(python test files, unsupported-language test files) the branch added or
    modified since ``base_sha``."""
    out = git_service._run(repo, "diff", "--name-only", "--diff-filter=AMR", base_sha, sha)
    paths = sorted(p for p in out.splitlines() if p.strip())
    return [p for p in paths if is_python_test(p)], [p for p in paths if is_unsupported_test(p)]


def _module(path: str) -> str:
    return path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")


def _file_of(test_id: str, files: list[str]) -> str | None:
    """Which run file a junit test id belongs to (longest module prefix)."""
    best = None
    for path in files:
        mod = _module(path)
        if test_id == mod or test_id.startswith(mod + "::") or test_id.startswith(mod + "."):
            if best is None or len(mod) > len(_module(best)):
                best = path
    return best


def junit_results(path: Path) -> dict[str, tuple[str, str]]:
    """Test id -> (outcome, first lines of the failure message). The id is the
    junit ``classname::name``; a collection error has no classname and is
    reported under the module name."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}
    out: dict[str, tuple[str, str]] = {}
    for case in root.iter("testcase"):
        name = case.get("name", "")
        if not name:
            continue
        classname = case.get("classname", "")
        test_id = f"{classname}::{name}" if classname else name
        node = case.find("failure")
        outcome = "failed"
        if node is None:
            node = case.find("error")
            outcome = "error"
        if node is None:
            outcome = "skipped" if case.find("skipped") is not None else "passed"
            out[test_id] = (outcome, "")
            continue
        text = (node.get("message") or "") + "\n" + (node.text or "")
        out[test_id] = (outcome, _excerpt(text))
    return out


def _excerpt(text: str) -> str:
    """The most telling lines: ``E   ...`` lines from the traceback, else the message."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    errors = [ln[1:].strip() for ln in lines if ln.startswith("E ")]
    picked = " | ".join(errors[:3]) if errors else (lines[0] if lines else "")
    return picked if len(picked) <= EXCERPT_CHARS else picked[:EXCERPT_CHARS].rstrip() + "..."


def run_files(
    workdir: Path,
    tmp_root: Path,
    *,
    target: str,
    ref: str,
    files: list[str],
    python: str,
    src_root: Path | None,
    timeout: float,
) -> tuple[BranchTestRun, dict[str, tuple[str, str]]]:
    """Run the repo's own test files in ``workdir`` (repo config and conftest
    apply; the generated-test runner's sandbox, env and network guard)."""
    # Resolved, so --rootdir matches the cwd pytest sees (macOS: /var is a
    # symlink to /private/var; a mismatch puts the worktree name into test ids).
    workdir = workdir.resolve()
    present = [f for f in files if (workdir / f).is_file()]
    if not present:
        return BranchTestRun(target=target, ref=ref, files=[], exit_code=0), {}  # type: ignore[arg-type]
    runner = tmp_root / "mg_runner.py"
    if not runner.exists():
        runner.write_text(verifier._RUNNER, encoding="utf-8")
    report = tmp_root / f"branch-tests-{target}.xml"
    report.unlink(missing_ok=True)
    home = tmp_root / "home"
    home.mkdir(exist_ok=True)
    env = verifier.clean_env(home, src_root, workdir if src_root is not None else None)
    env["MG_CWD_ON_PATH"] = "1"  # like `python -m pytest`: the checkout itself is importable
    cmd = [
        *verifier._sandbox_prefix(tmp_root),
        python,
        "-I",
        str(runner),
        "-q",
        "-p",
        "no:cacheprovider",
        "--maxfail=100000",  # undo a repo's -x: every test of the files gets a verdict
        f"--rootdir={workdir}",
        f"--junitxml={report}",
        *present,
    ]
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    timed_out = False
    try:
        proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        verifier._kill_group(proc)
    results = junit_results(report)
    counts = {k: sum(1 for o, _ in results.values() if o == k)
              for k in ("passed", "failed", "error")}
    run = BranchTestRun(
        target=target,  # type: ignore[arg-type]
        ref=ref,
        files=present,
        exit_code=None if timed_out else proc.returncode,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["error"],
        timed_out=timed_out,
        duration_s=round(time.monotonic() - started, 2),
    )
    return run, results


def _own_passed(results: dict[str, tuple[str, str]], files: list[str]) -> dict[str, str]:
    """Test id -> file, for tests from ``files`` that passed."""
    out = {}
    for test_id, (outcome, _) in results.items():
        if outcome == "passed":
            path = _file_of(test_id, files)
            if path:
                out[test_id] = path
    return out


def _merge_failures(
    merge: dict[str, tuple[str, str]], passed_on_own: dict[str, str], files: list[str]
) -> dict[str, tuple[str, str]]:
    """Merge failures of tests that passed on their own branch. A file that
    collected and passed on its branch but fails to import on the merge
    (collection error under the module name) counts for that whole file."""
    own_files = set(passed_on_own.values())
    out: dict[str, tuple[str, str]] = {}
    for test_id, (outcome, excerpt) in merge.items():
        if outcome not in _STATUSES_FAILING:
            continue
        if test_id in passed_on_own:
            out[test_id] = (passed_on_own[test_id], excerpt)
            continue
        path = _file_of(test_id, files)
        if path in own_files and test_id == _module(path):
            out[test_id] = (path, excerpt)  # module-level collection error
    return out


def check_pair(repo: Path, base: str, branch_a: str, branch_b: str) -> BranchTests:
    """Run both branches' own tests on the merge of A+B. See the module docstring."""
    started = time.monotonic()

    def finish(status: str, reason: str, **extra) -> BranchTests:
        return BranchTests(
            status=status,  # type: ignore[arg-type]
            reason=reason,
            sandbox=verifier.sandbox_kind(),
            duration_s=round(time.monotonic() - started, 2),
            **extra,
        )

    try:
        sha_a = git_service.rev_parse(repo, branch_a)
        sha_b = git_service.rev_parse(repo, branch_b)
        sha_base = git_service.merge_base(repo, sha_a, sha_b) or git_service.rev_parse(repo, base)
        py_a, other_a = changed_test_files(repo, sha_base, sha_a)
        py_b, other_b = changed_test_files(repo, sha_base, sha_b)
    except git_service.GitError as exc:
        return finish("error", f"git: {exc}"[:300])
    cap = max(1, int(settings.branch_tests_max_files))
    truncated = len(py_a) > cap or len(py_b) > cap
    py_a, py_b = py_a[:cap], py_b[:cap]
    unsupported = sorted(set(other_a) | set(other_b))
    info = {"files_a": py_a, "files_b": py_b, "unsupported_files": unsupported[:20]}
    if not (py_a or py_b):
        if unsupported:
            return finish("unsupported",
                          f"only non-Python test files changed ({len(unsupported)}); "
                          "branch tests run pytest only", **info)
        return finish("skipped", "neither branch added or modified a test file", **info)

    timeout = float(settings.branch_tests_timeout_seconds)
    python = verifier.project_python(repo)
    src_root = repo if python != sys.executable else None
    opts = {"python": python, "src_root": src_root, "timeout": timeout}
    union = sorted(set(py_a) | set(py_b))
    runs: list[BranchTestRun] = []
    try:
        with verifier._Worktrees(repo, sha_a, sha_b, sha_base) as wt:
            if wt.merge_conflict:
                return finish("skipped", "textual merge conflict: git merge --no-commit failed",
                              merge_conflict=True, **info)
            run_a, res_a = run_files(wt.paths["a"], wt.tmp, target="a", ref=branch_a,
                                     files=py_a, **opts)
            run_b, res_b = run_files(wt.paths["b"], wt.tmp, target="b", ref=branch_b,
                                     files=py_b, **opts)
            run_m, res_m = run_files(wt.paths["merge"], wt.tmp, target="merge",
                                     ref=f"{branch_a}+{branch_b}", files=union, **opts)
            runs += [run_a, run_b, run_m]
            passed_own = {**_own_passed(res_a, py_a), **_own_passed(res_b, py_b)}
            owner = {**{p: branch_a for p in py_a}, **{p: branch_b for p in py_b if p not in py_a}}
            both = set(py_a) & set(py_b)
            candidates = _merge_failures(res_m, passed_own, union)
            if candidates:
                files = sorted({path for path, _ in candidates.values()})
                run_base, res_base = run_files(wt.paths["base"], wt.tmp, target="base",
                                               ref=f"merge-base {sha_base[:7]}", files=files, **opts)
                run_again, res_again = run_files(wt.paths["merge"], wt.tmp, target="merge-rerun",
                                                 ref=f"{branch_a}+{branch_b}", files=files, **opts)
                runs += [run_base, run_again]
                for test_id in list(candidates):
                    if res_base.get(test_id, ("absent", ""))[0] in _STATUSES_FAILING:
                        del candidates[test_id]  # already failing on the merge-base
                    elif res_again.get(test_id, ("absent", ""))[0] not in _STATUSES_FAILING:
                        del candidates[test_id]  # flaky: passed on the second merge run
    except git_service.GitError as exc:
        return finish("error", f"git: {exc}"[:300], runs=runs, **info)
    except OSError as exc:
        return finish("error", f"runner: {exc}"[:300], runs=runs, **info)

    note = f"; only the first {cap} test files per branch ran" if truncated else ""
    if unsupported:
        note += f"; {len(unsupported)} non-Python test file(s) not run"
    confirming = [
        BranchTestFailure(
            test_id=test_id,
            branch="both" if path in both else owner.get(path, ""),
            file=path,
            excerpt=excerpt,
        )
        for test_id, (path, excerpt) in sorted(candidates.items())
    ]
    if confirming:
        first = confirming[0]
        return finish(
            "confirmed",
            f"{len(confirming)} test(s) from the branches' own test files pass on their branch "
            f"and fail on the merge of A+B (not on the merge-base, twice on the merge), e.g. "
            f"{first.test_id}" + note,
            runs=runs, confirming=confirming, **info,
        )
    if any(r.timed_out for r in runs):
        return finish("error", f"a branch test run timed out after {timeout:.0f}s" + note,
                      runs=runs, **info)
    if not passed_own:
        return finish("passed", "no branch test passed on its own branch, nothing to compare"
                      + note, runs=runs, **info)
    return finish(
        "passed",
        f"all {len(passed_own)} branch test(s) that pass on their own branch also pass on "
        "the merge (or already fail on the merge-base)" + note,
        runs=runs, **info,
    )


def eligible_pairs(pairs) -> list:
    """Prefilter candidates, highest signal first, capped."""
    limit = max(0, int(settings.branch_tests_max_pairs))
    cands = [p for p in pairs if p.prefilter_candidate]
    cands.sort(key=lambda p: (p.overlapping_files, len(p.symbol_overlap), p.shared_files),
               reverse=True)
    return cands[:limit]
