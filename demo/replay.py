#!/usr/bin/env python3
"""Replay REAL semantic merge conflicts from open-source repos (Hall of Fame).

Each case clones a public repo at pinned SHAs into ``demo/<case>`` (gitignored)
with three local branches and no remote:

    main      = merge-base of the two PRs
    <PR A>    = PR A head (as it was when CI went green)
    <PR B>    = PR B head

    python3 demo/replay.py --list                 # the registry
    python3 demo/replay.py --case kiss            # clone + branches + venv
    python3 demo/replay.py --case kiss --prove    # ... and replay the tests
    python3 demo/replay.py --case kiss --no-venv  # branches only (no deps)

``--prove`` runs the case's tests on main, A and B, merges B into A with
``git merge --no-ff --no-commit`` (must be clean), runs them on A+B, then
applies the upstream fix commit's test-file change and runs them again.
Test paths that do not exist on a side are reported and skipped.

The venv lives in ``demo/<case>/.venv``; MergeGuard's verify-by-test uses it
automatically. JS/TS cases (runner "vitest", e.g. qwen-code) install their npm
deps once on PR A (``npm ci`` + the build their tests import) and run vitest;
MergeGuard's verify-by-test is Python-only, so for them it reports the model
triage alone. Nothing is ever pushed: the ``origin`` remote is removed.
See docs/HALL_OF_FAME.md for the story of each case.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Case:
    name: str
    title: str
    upstream: str
    base: str
    pr_a: tuple[str, str]  # (local branch name, head sha)
    pr_b: tuple[str, str]
    fix: str  # upstream commit that repaired main
    fix_paths: tuple[str, ...]  # files taken from the fix commit
    test_args: tuple[str, ...]
    venv: str = "uv-sync"  # "uv-sync" (project lock) or "pytest" (stdlib project + pytest)
    uv_args: tuple[str, ...] = ()
    fetch: tuple[str, ...] = ()  # extra refspecs (PR heads not on any branch)
    marker: str = ""
    pytest_pin: str = "pytest==9.1.1"
    depth: int = 0  # >0: shallow fetch of just the pinned commits (huge repos)
    # runner "vitest": a JS/TS case. MergeGuard's verify-by-test is Python-only,
    # so these replays exist to reproduce the break and feed the model triage.
    runner: str = "pytest"
    test_cwd: str = ""  # sub-directory the test command runs in
    setup: tuple[tuple[str, ...], ...] = ()  # deps step for non-pytest runners (run on PR A)

    @property
    def dest(self) -> Path:
        return HERE / self.name


CASES: dict[str, Case] = {
    c.name: c
    for c in (
        Case(
            name="real-case-1",
            title="pymc-labs/daimon PR #14 x PR #17 (pydantic required fields vs a new fixture)",
            upstream="https://github.com/pymc-labs/daimon.git",
            base="1933b2444b2d6e8ec1ee739d0d0109571d27406e",
            pr_a=("pr-14-sync-fixes", "1c4f61047aaff750c70b358209a3196715f42c0f"),
            pr_b=("pr-17-deploy-review-findings", "7175c4d315ef8033d15afc8206249a1bd18991c4"),
            fix="239fdb55ac47a9e655291b896f25807d7efd48be",
            fix_paths=("packages/adapters/discord/tests/test_branding.py",),
            test_args=("packages/adapters/discord/tests", "-k", "branding or privacy_panel or purge"),
            marker=".mergeguard-replay-case-1",  # same marker as demo/replay_case_1.py
        ),
        Case(
            name="kiss",
            title="ThinkersJournal/KISS PR #311 x PR #315 (read_floor now returns a tuple)",
            upstream="https://github.com/ThinkersJournal/KISS.git",
            base="9174f952664ef1ea79220a84de8927efe0d1c8de",
            pr_a=("pr-311-proven-batch-1", "fc97ccc39e9bb35806e1dc3df3d1790ef9a5549a"),
            pr_b=("pr-315-floor-dimensions", "bef61e271b1801699b55175b5cc2c4ce1eed309b"),
            fix="9c8543bd02a458747056b5f6a4dea3750fcf8dd6",
            fix_paths=("tools/test_kiss_proven_marker.py",),
            # Deselected on every side, unrelated to the conflict: #315's
            # ValueIsComparedTest drives the ratchet against an origin/main ref
            # (a local-only replay has no remote, so it answers INCONCLUSIVE), and
            # test_kiss_ratchet_discrimination fails identically on main, A and B
            # on macOS (its git-ref controls read None there; it passes on Linux).
            test_args=("tools", "-k",
                       "not ValueIsComparedTest and not test_kiss_ratchet_discrimination"),
            venv="pytest",  # tools/ is stdlib-only Python; the tests need just pytest
            # Both PRs were squash-merged: their heads live only under refs/pull.
            fetch=("+refs/pull/311/head:refs/replay/pr-311", "+refs/pull/315/head:refs/replay/pr-315"),
        ),
        Case(
            name="elastic",
            title="elastic/observability-migration-platform PR #381 x PR #383 (nested-agg lowering vs a pinned query)",
            upstream="https://github.com/elastic/observability-migration-platform.git",
            base="c0bd6523f4e722242c2334499856a7e542c91e24",
            pr_a=("pr-381-nested-agg-counter-ts", "d45451c755f12b9aabd7a8848fc75116802e416f"),
            pr_b=("pr-383-nested-legend-inner-group", "c940de30ca79bf18ef3f281644420df6dc53f75d"),
            fix="1dcac99d630c3473c5c0a5ea38417542658b8a04",
            fix_paths=("tests/test_issue382_nested_legend_inner_group.py",),
            test_args=("tests/test_issue382_nested_legend_inner_group.py", "tests/test_migrate.py"),
            uv_args=("--extra", "dev"),
        ),
        Case(
            name="qwen-code",
            title="QwenLM/qwen-code PR #8947 x PR #8933 (TypeScript: historyPageSize now needs historyReplay)",
            upstream="https://github.com/QwenLM/qwen-code.git",
            base="86a474ba62a50d25e65deac1a0d5111c4a5bb90d",
            pr_a=("pr-8947-acp-resource-guards", "e2c6af2526c38152d859d52b572b6dd9b0855572"),
            pr_b=("pr-8933-restore-request-shapes", "d47eba4f41a3f254bee13add58ceb6d965bc0483"),
            fix="ec3b2cbb660c38db935fd593a551bbf97ff4f430",
            fix_paths=("packages/acp-bridge/src/bridge.test.ts",),
            test_args=("src/bridge.test.ts",),
            depth=16,  # ~1.3 GB repo; the merge-base is 11 commits behind PR A
            runner="vitest",
            test_cwd="packages/acp-bridge",
            setup=(("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"),
                   ("npm", "run", "build", "--workspace=packages/core")),
        ),
    )
}


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=check
    )


def _marker(case: Case) -> str:
    return case.marker or f".mergeguard-replay-{case.name}"


def build(case: Case, dest: Path, fresh: bool) -> None:
    marker = _marker(case)
    if fresh and dest.exists():
        shutil.rmtree(dest)
    if not (dest / marker).exists():
        if dest.exists():
            sys.exit(f"{dest} exists but is not a replay checkout; pass --fresh to replace it")
        if case.depth:
            print(f"fetching the pinned commits of {case.upstream} (depth {case.depth}) -> {dest}")
            subprocess.run(["git", "init", "--quiet", str(dest)], check=True)
            git(dest, "remote", "add", "origin", case.upstream)
            for shas, depth in (((case.pr_a[1], case.pr_b[1]), case.depth), ((case.fix,), 2)):
                subprocess.run(["git", "-C", str(dest), "fetch", "--quiet", f"--depth={depth}",
                                "origin", *shas], check=True)
        else:
            print(f"cloning {case.upstream} -> {dest}")
            subprocess.run(["git", "clone", "--quiet", "--no-checkout", case.upstream, str(dest)],
                           check=True)
        if case.fetch:
            subprocess.run(["git", "-C", str(dest), "fetch", "--quiet", "origin", *case.fetch],
                           check=True)
        (dest / ".git" / "info").mkdir(exist_ok=True)
        with open(dest / ".git" / "info" / "exclude", "a", encoding="utf-8") as fh:
            fh.write(f"\n{marker}\n.venv/\nnode_modules/\n")
        (dest / marker).write_text(f"{case.upstream}\n", encoding="utf-8")
    for sha in (case.base, case.pr_a[1], case.pr_b[1], case.fix):
        git(dest, "cat-file", "-e", f"{sha}^{{commit}}")
    git(dest, "checkout", "--quiet", "--force", "-B", "main", case.base)
    for name, sha in (case.pr_a, case.pr_b):
        git(dest, "branch", "--force", name, sha)
    keep = {"main", case.pr_a[0], case.pr_b[0]}
    for name in git(dest, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split():
        if name not in keep:
            git(dest, "branch", "-D", name)
    if "origin" in git(dest, "remote").stdout.split():
        git(dest, "remote", "remove", "origin")  # local only: nothing to push to
    git(dest, "config", "user.name", "MergeGuard Replay")
    git(dest, "config", "user.email", "replay@mergeguard.local")
    for name, sha in (("main", case.base), case.pr_a, case.pr_b):
        print(f"  {name:32s} {git(dest, 'log', '-1', '--format=%h %s', sha).stdout.strip()[:90]}")


def make_venv(case: Case, dest: Path) -> None:
    if case.setup:
        git(dest, "checkout", "--quiet", "--force", case.pr_a[0])
        try:
            for cmd in case.setup:
                print(" ".join(cmd) + " ...")
                subprocess.run(list(cmd), cwd=dest, check=True)
        finally:
            git(dest, "checkout", "--quiet", "--force", "main")
        return
    uv = shutil.which("uv")
    if case.venv == "uv-sync":
        if not uv:
            sys.exit("uv not found: install it (https://docs.astral.sh/uv/) or pass --no-venv")
        print(f"uv sync --frozen {' '.join(case.uv_args)} (project venv for the tests) ...")
        subprocess.run([uv, "sync", "--frozen", "--quiet", *case.uv_args], cwd=dest, check=True)
        return
    py = dest / ".venv" / "bin" / "python"
    if not py.exists():
        print(f"venv + {case.pytest_pin} ...")
        if uv:
            subprocess.run([uv, "venv", "--quiet", str(dest / ".venv")], check=True)
            subprocess.run([uv, "pip", "install", "--quiet", "--python", str(py), case.pytest_pin],
                           check=True)
        else:
            subprocess.run([sys.executable, "-m", "venv", str(dest / ".venv")], check=True)
            subprocess.run([str(py), "-m", "pip", "install", "--quiet", case.pytest_pin], check=True)


def _present(dest: Path, args: tuple[str, ...]) -> list[str]:
    out, missing = [], []
    for i, arg in enumerate(args):
        is_path = not arg.startswith("-") and (i == 0 or args[i - 1] != "-k") and (
            "/" in arg or arg.endswith(".py"))
        if is_path and not (dest / arg.split("::")[0]).exists():
            missing.append(arg)
        else:
            out.append(arg)
    if missing:
        print(f"      (not on this side, skipped: {' '.join(missing)})")
    return out


def _run_tests(case: Case, dest: Path, label: str) -> int:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
    print(f"  {label}")
    cwd = dest / case.test_cwd
    if case.runner == "vitest":
        cmd = ["npx", "vitest", "run", *case.test_args]
    else:
        cmd = [str(dest / ".venv" / "bin" / "python"), "-m", "pytest", "-p", "no:cacheprovider",
               "-rfE", *_present(dest, case.test_args)]
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    out = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout + "\n" + proc.stderr)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if case.runner == "vitest":
        totals = [ln.strip() for ln in lines if ln.strip().startswith("Tests ")]
        failed = [ln.strip() for ln in lines if ln.strip().startswith("FAIL ")]
    else:
        totals = [ln for ln in lines if re.search(r"\d+ (passed|failed|error)", ln)]
        failed = [ln for ln in lines if ln.startswith(("FAILED", "ERROR", "SUBFAIL"))]
    summary = (totals or lines or [proc.stderr.strip()[-300:]])[-1].strip("= ")
    print(f"      exit={proc.returncode}  {summary}")
    for ln in failed:
        print(f"      {ln}")
    return proc.returncode


def prove(case: Case, dest: Path) -> bool:
    head = git(dest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    a, b = case.pr_a, case.pr_b
    ok = True
    try:
        merge_base = git(dest, "merge-base", a[1], b[1]).stdout.strip()
        print(f"merge-base({a[0]}, {b[0]}) = {merge_base[:8]} (main = {case.base[:8]})")
        shared = set(git(dest, "diff", "--name-only", case.base, a[1]).stdout.split()) & set(
            git(dest, "diff", "--name-only", case.base, b[1]).stdout.split()
        )
        print(f"files changed by both PRs: {len(shared)} {sorted(shared) if shared else ''}")
        print(f"{case.runner} " + " ".join(case.test_args)
              + (f"  (in {case.test_cwd})" if case.test_cwd else ""))
        for label, sha in (("main (merge-base)", case.base), (a[0], a[1]), (b[0], b[1])):
            git(dest, "checkout", "--quiet", "--force", "--detach", sha)
            ok &= _run_tests(case, dest, label) == 0
        git(dest, "checkout", "--quiet", "--force", "--detach", a[1])
        merge = git(dest, "merge", "--no-ff", "--no-commit", b[1], check=False)
        print(f"git merge --no-ff --no-commit {b[0]} into {a[0]}: exit={merge.returncode}")
        print("  " + (merge.stdout + merge.stderr).strip().replace("\n", "\n  "))
        if merge.returncode != 0:
            print("RESULT: textual conflict, not a clean semantic case")
            return False
        merged_rc = _run_tests(case, dest, "A+B merged (clean)")
        fix = git(dest, "show", "--format=", case.fix, "--", *case.fix_paths)
        applied = subprocess.run(["git", "-C", str(dest), "apply"], input=fix.stdout, text=True)
        fixed_rc = None
        if applied.returncode == 0:
            fixed_rc = _run_tests(case, dest, f"A+B + upstream fix {case.fix[:8]}")
        reproduced = ok and merged_rc != 0 and fixed_rc == 0
        print("RESULT: " + ("reproduced (main, A, B pass; clean merge; A+B fails; fix passes)"
                            if reproduced else "NOT reproduced"))
        return reproduced
    finally:
        git(dest, "merge", "--abort", check=False)
        git(dest, "reset", "--quiet", "--hard", check=False)
        git(dest, "checkout", "--quiet", "--force", head if head != "HEAD" else "main", check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--case", choices=sorted(CASES))
    parser.add_argument("--list", action="store_true", help="list the cases")
    parser.add_argument("--dest", type=Path, help="default: demo/<case>")
    parser.add_argument("--fresh", action="store_true", help="delete and re-clone")
    parser.add_argument("--no-venv", action="store_true", help="skip the venv")
    parser.add_argument("--prove", action="store_true", help="replay the tests on A, B, A+B, fix")
    args = parser.parse_args()
    if args.list or not args.case:
        for c in CASES.values():
            print(f"{c.name:12s} {c.title}")
        return 0 if args.list else 2
    case = CASES[args.case]
    dest = (args.dest or case.dest).resolve()
    build(case, dest, args.fresh)
    if not args.no_venv:
        make_venv(case, dest)
    rc = 0
    if args.prove:
        rc = 0 if prove(case, dest) else 1
    print(f"\nreplay repo: {dest}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
