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
suppress it. Python test files run with pytest; JS/TS test files with the
package's vitest or jest (``js_tests``: shared dependency install per lockfile,
only the workspace builds the tests need). Only runs where verify-by-test may
run (local repos under the allowed roots).
"""
from __future__ import annotations

import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from app.config import settings
from app.schemas import BranchTestFailure, BranchTestRun, BranchTests
from app.services import git_service, js_tests, verifier

EXCERPT_CHARS = 300
_STATUSES_FAILING = ("failed", "error")


def is_python_test(path: str) -> bool:
    name = Path(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def is_js_test(path: str) -> bool:
    return js_tests.is_js_test(path)


def changed_test_files(repo: Path, base_sha: str, sha: str) -> tuple[list[str], list[str]]:
    """(python test files, JS/TS test files) the branch added or modified since
    ``base_sha``."""
    out = git_service._run(repo, "diff", "--name-only", "--diff-filter=AMR", base_sha, sha)
    paths = sorted(p for p in out.splitlines() if p.strip())
    return [p for p in paths if is_python_test(p)], [p for p in paths if is_js_test(p)]


def _module(path: str) -> str:
    return path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")


def _file_of(test_id: str, files: list[str]) -> str | None:
    """Which run file a junit test id belongs to (longest module prefix)."""
    best = None
    for path in files:
        if test_id == path or test_id.startswith(path + "::"):
            return path  # JS ids are "<repo-relative path>::<suite> > <test>"
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
        if path in own_files and test_id in (_module(path), path):
            out[test_id] = (path, excerpt)  # module-level collection / load error
    return out


class _JsSide:
    """JS/TS test runs for one pair: per-worktree dependency linking and
    builds (done once per worktree), then vitest/jest per package."""

    def __init__(self, repo: Path, groups: dict[tuple[str, str], list[str]], node: str,
                 timeout: float, started: float) -> None:
        self.repo = repo
        self.groups = groups  # (runner, package dir) -> files
        self.node = node
        self.timeout = timeout
        self.build_deadline = started + float(settings.branch_tests_js_build_seconds)
        self.ready: dict[str, dict[str, str] | str] = {}  # worktree -> workspace map | problem
        self.setup: list[str] = []
        self.install_s = 0.0
        self.build_s = 0.0
        self._installs: set[str] = set()

    def _prepare(self, workdir: Path, tmp: Path) -> dict[str, str] | str:
        key = str(workdir)
        if key in self.ready:
            return self.ready[key]
        try:
            prep = js_tests.prepare_deps(self.repo, workdir, self.node)
            if prep.key not in self._installs:
                self._installs.add(prep.key)
                if prep.cached:
                    self.setup.append(f"{prep.manager} dependencies reused from cache ({prep.key})")
                else:
                    self.install_s += prep.seconds
                    self.setup.append(f"{prep.manager} install ({prep.key}, scripts disabled): "
                                      f"{prep.seconds:.1f}s")
            workspace = js_tests.link_deps(prep, workdir)
            log = js_tests.BuildLog()
            for _runner, pkg in self.groups:
                js_tests.ensure_builds(workdir, tmp, pkg, workspace, prep.manager, self.node,
                                       js_tests._js_root() / "builds", log, self.build_deadline)
            self.build_s += log.seconds
            for note in log.notes:
                if note not in self.setup:
                    self.setup.append(note)
            result: dict[str, str] | str = workspace
        except (js_tests.JsSetupError, git_service.GitError, OSError) as exc:
            result = f"JS setup failed: {exc}"[:300]
            if result not in self.setup:
                self.setup.append(result)
        self.ready[key] = result
        return result

    def run(self, workdir: Path, tmp: Path, *, target: str, ref: str, files: list[str]
            ) -> tuple[list[BranchTestRun], dict[str, tuple[str, str]]]:
        wanted = set(files)
        todo = [(g, [f for f in fs if f in wanted and (workdir / f).is_file()])
                for g, fs in self.groups.items()]
        todo = [(g, fs) for g, fs in todo if fs]
        if not todo:
            return [], {}
        ready = self._prepare(workdir, tmp)
        runs: list[BranchTestRun] = []
        results: dict[str, tuple[str, str]] = {}
        for (runner, pkg), fs in todo:
            tag = f"{target}-{len(runs)}"
            if isinstance(ready, str):
                runs.append(BranchTestRun(target=target, ref=ref, files=fs, exit_code=None,  # type: ignore[arg-type]
                                          runner=runner, package=pkg, problem=ready))
                continue
            res, code, timed_out, secs, problem = js_tests.run_tests(
                workdir, tmp, runner=runner, pkg_rel=pkg, files=fs, node=self.node,
                timeout=self.timeout, tag=tag)
            counts = {k: sum(1 for o, _ in res.values() if o == k)
                      for k in ("passed", "failed", "error")}
            runs.append(BranchTestRun(
                target=target, ref=ref, files=fs, exit_code=code,  # type: ignore[arg-type]
                passed=counts["passed"], failed=counts["failed"], errors=counts["error"],
                timed_out=timed_out, duration_s=secs, runner=runner, package=pkg,
                problem=problem,
            ))
            results.update(res)
        return runs, results


def _js_groups(root: Path, files_a: list[str], files_b: list[str]
               ) -> tuple[dict[tuple[str, str], list[str]], list[str], str]:
    """Group JS test files by (runner, package dir), most-shared packages first,
    capped. Returns (groups, files not run, note)."""
    both = set(files_a) & set(files_b)
    groups: dict[tuple[str, str], list[str]] = {}
    unrunnable: list[str] = []
    for path in sorted(set(files_a) | set(files_b)):
        found = js_tests.runner_for(root, path) if (root / path).is_file() else None
        if found is None:
            unrunnable.append(path)
        else:
            groups.setdefault(found, []).append(path)

    def rank(item: tuple[tuple[str, str], list[str]]) -> tuple[int, int, int]:
        fs = item[1]
        from_a = any(f in files_a for f in fs)
        from_b = any(f in files_b for f in fs)
        return (int(from_a and from_b), sum(f in both for f in fs), len(fs))

    ordered = sorted(groups.items(), key=rank, reverse=True)
    cap = max(1, int(settings.branch_tests_js_max_packages))
    kept = dict(ordered[:cap])
    dropped = [f for _, fs in ordered[cap:] for f in fs]
    note = ""
    if dropped:
        note = f"; JS test files in {len(ordered) - cap} more package(s) not run (cap {cap})"
    return kept, unrunnable + dropped, note


def check_pair(repo: Path, base: str, branch_a: str, branch_b: str) -> BranchTests:
    """Run both branches' own tests on the merge of A+B. See the module docstring."""
    started = time.monotonic()
    js_side: _JsSide | None = None

    def finish(status: str, reason: str, **extra) -> BranchTests:
        sandbox = verifier.sandbox_kind()
        if js_side is not None:
            sandbox = f"{sandbox}; JS test runs: localhost allowed, no other network"
            extra.setdefault("setup", js_side.setup)
            extra.setdefault("install_s", round(js_side.install_s, 1))
            extra.setdefault("build_s", round(js_side.build_s, 1))
        return BranchTests(
            status=status,  # type: ignore[arg-type]
            reason=reason,
            sandbox=sandbox,
            duration_s=round(time.monotonic() - started, 2),
            **extra,
        )

    try:
        sha_a = git_service.rev_parse(repo, branch_a)
        sha_b = git_service.rev_parse(repo, branch_b)
        sha_base = git_service.merge_base(repo, sha_a, sha_b) or git_service.rev_parse(repo, base)
        py_a, js_a = changed_test_files(repo, sha_base, sha_a)
        py_b, js_b = changed_test_files(repo, sha_base, sha_b)
    except git_service.GitError as exc:
        return finish("error", f"git: {exc}"[:300])
    cap = max(1, int(settings.branch_tests_max_files))
    truncated = any(len(x) > cap for x in (py_a, py_b, js_a, js_b))
    py_a, py_b, js_a, js_b = py_a[:cap], py_b[:cap], js_a[:cap], js_b[:cap]
    node = js_tests.node_binary() if (js_a or js_b) and settings.branch_tests_js_enabled else None
    js_reason = ""
    if (js_a or js_b) and not settings.branch_tests_js_enabled:
        js_reason = "JS/TS branch tests are disabled"
    elif (js_a or js_b) and node is None:
        js_reason = "no node binary found for the JS/TS test files"
    if js_reason:
        unsupported, js_a, js_b = sorted(set(js_a) | set(js_b)), [], []
    else:
        unsupported = []
    info = {"files_a": py_a + js_a, "files_b": py_b + js_b, "unsupported_files": unsupported[:20]}
    if not (py_a or py_b or js_a or js_b):
        if unsupported:
            return finish("unsupported", f"{js_reason} ({len(unsupported)} file(s))", **info)
        return finish("skipped", "neither branch added or modified a test file", **info)

    timeout = float(settings.branch_tests_timeout_seconds)
    python = verifier.project_python(repo)
    src_root = repo if python != sys.executable else None
    opts = {"python": python, "src_root": src_root, "timeout": timeout}
    note = f"; only the first {cap} test files per branch and language ran" if truncated else ""
    runs: list[BranchTestRun] = []
    try:
        with verifier._Worktrees(repo, sha_a, sha_b, sha_base) as wt:
            if wt.merge_conflict:
                return finish("skipped", "textual merge conflict: git merge --no-commit failed",
                              merge_conflict=True, **info)
            if js_a or js_b:
                groups, not_run, js_note = _js_groups(wt.paths["merge"], js_a, js_b)
                note += js_note
                if not_run:
                    unsupported = sorted(set(unsupported) | set(not_run))
                    info["unsupported_files"] = unsupported[:20]
                js_a = [f for f in js_a if f not in not_run]
                js_b = [f for f in js_b if f not in not_run]
                if groups:
                    js_side = _JsSide(repo, groups, node or "", timeout, started)
            if not (py_a or py_b or js_a or js_b):
                return finish("unsupported",
                              f"no vitest/jest setup found for the {len(unsupported)} JS/TS "
                              "test file(s)", **info)
            files_a, files_b = py_a + js_a, py_b + js_b
            union = sorted(set(files_a) | set(files_b))

            def run(target: str, ref: str, files: list[str], workdir: Path
                    ) -> dict[str, tuple[str, str]]:
                results: dict[str, tuple[str, str]] = {}
                py = [f for f in files if is_python_test(f)]
                if py or not js_side:
                    run_py, res = run_files(workdir, wt.tmp, target=target, ref=ref, files=py, **opts)
                    if py:
                        runs.append(run_py)
                    results.update(res)
                if js_side is not None:
                    js_runs, res = js_side.run(workdir, wt.tmp, target=target, ref=ref,
                                               files=[f for f in files if is_js_test(f)])
                    runs.extend(js_runs)
                    results.update(res)
                return results

            res_a = run("a", branch_a, files_a, wt.paths["a"])
            res_b = run("b", branch_b, files_b, wt.paths["b"])
            res_m = run("merge", f"{branch_a}+{branch_b}", union, wt.paths["merge"])
            passed_own = {**_own_passed(res_a, files_a), **_own_passed(res_b, files_b)}
            owner = {**{p: branch_a for p in files_a},
                     **{p: branch_b for p in files_b if p not in files_a}}
            both = set(files_a) & set(files_b)
            candidates = _merge_failures(res_m, passed_own, union)
            if candidates:
                files = sorted({path for path, _ in candidates.values()})
                res_base = run("base", f"merge-base {sha_base[:7]}", files, wt.paths["base"])
                res_again = run("merge-rerun", f"{branch_a}+{branch_b}", files, wt.paths["merge"])
                for test_id in list(candidates):
                    if res_base.get(test_id, ("absent", ""))[0] in _STATUSES_FAILING:
                        del candidates[test_id]  # already failing on the merge-base
                    elif res_again.get(test_id, ("absent", ""))[0] not in _STATUSES_FAILING:
                        del candidates[test_id]  # flaky: passed on the second merge run
    except git_service.GitError as exc:
        return finish("error", f"git: {exc}"[:300], runs=runs, **info)
    except OSError as exc:
        return finish("error", f"runner: {exc}"[:300], runs=runs, **info)

    if unsupported:
        note += f"; {len(unsupported)} test file(s) not run"
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
    problems = [r.problem for r in runs if r.problem and not (r.passed or r.failed or r.errors)]
    if not passed_own and problems:
        return finish("error", f"branch tests could not run: {problems[0]}"[:300] + note,
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
