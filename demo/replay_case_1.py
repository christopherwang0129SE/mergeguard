#!/usr/bin/env python3
"""Replay a REAL semantic merge conflict from an open-source repo.

Case 1: pymc-labs/daimon (MIT), PR #14 x PR #17, July 2026.

- PR #14 (feat/sync-fixes, head 1c4f6104) added two REQUIRED fields,
  ``slack_user_tokens`` and ``slack_turn_contexts``, to the pydantic model
  ``PurgePreview`` in ``packages/core/daimon/core/privacy.py`` and updated
  every fixture that existed on its base.
- PR #17 (fix/deploy-review-findings, head 7175c4d3) added a new test module,
  ``packages/adapters/discord/tests/test_branding.py``, whose ``_make_preview()``
  builds a ``PurgePreview`` with the OLD field set (imported through the
  re-export in ``daimon.adapters.discord.privacy_panel.state``).

Both PRs were green in CI on their own (pytest-discord: success on both head
commits). They touch no common file, so ``git merge`` is clean. After both
landed, main's pytest-discord job failed (2 tests in test_branding.py:
pydantic ValidationError, fields required) until upstream commit 239fdb55
added the two fields to the fixture ("Semantic merge conflict: ... neither
branch's CI saw the other").

This script clones the public repo at pinned SHAs into ``demo/real-case-1``
(gitignored) with three local branches and no remote:

    main                          = 1933b244 (merge-base of both PRs)
    pr-14-sync-fixes              = 1c4f6104 (PR #14 head)
    pr-17-deploy-review-findings  = 7175c4d3 (PR #17 head)

    python3 demo/replay_case_1.py            # clone + branches + uv venv
    python3 demo/replay_case_1.py --prove    # ... and replay the tests
    python3 demo/replay_case_1.py --no-venv  # branches only (no deps)

The project venv (``uv sync --frozen``, needs ``uv`` and network once) lives in
``demo/real-case-1/.venv``; MergeGuard's verify-by-test uses it automatically.
No Postgres is needed for the tests used here (the DB-backed daimon tests are
not run).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

UPSTREAM = "https://github.com/pymc-labs/daimon.git"
BASE = "1933b2444b2d6e8ec1ee739d0d0109571d27406e"
PR_A = ("pr-14-sync-fixes", "1c4f61047aaff750c70b358209a3196715f42c0f")
PR_B = ("pr-17-deploy-review-findings", "7175c4d315ef8033d15afc8206249a1bd18991c4")
UPSTREAM_FIX = "239fdb55ac47a9e655291b896f25807d7efd48be"
MARKER = ".mergeguard-replay-case-1"

# Non-DB tests that exercise PurgePreview and the branding change.
TEST_ARGS = [
    "packages/adapters/discord/tests",
    "-k", "branding or privacy_panel or purge",
]

HERE = Path(__file__).resolve().parent
DEFAULT_DEST = HERE / "real-case-1"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=check
    )


def build(dest: Path, fresh: bool) -> None:
    if fresh and dest.exists():
        shutil.rmtree(dest)
    if not (dest / MARKER).exists():
        if dest.exists():
            sys.exit(f"{dest} exists but is not a replay checkout; pass --fresh to replace it")
        print(f"cloning {UPSTREAM} -> {dest}")
        subprocess.run(["git", "clone", "--quiet", "--no-checkout", UPSTREAM, str(dest)], check=True)
        (dest / ".git" / "info").mkdir(exist_ok=True)
        with open(dest / ".git" / "info" / "exclude", "a", encoding="utf-8") as fh:
            fh.write(f"\n{MARKER}\n")
        (dest / MARKER).write_text(f"{UPSTREAM}\n", encoding="utf-8")
    for sha in (BASE, PR_A[1], PR_B[1], UPSTREAM_FIX):
        git(dest, "cat-file", "-e", f"{sha}^{{commit}}")
    git(dest, "checkout", "--quiet", "--force", "-B", "main", BASE)
    for name, sha in (PR_A, PR_B):
        git(dest, "branch", "--force", name, sha)
    keep = {"main", PR_A[0], PR_B[0]}
    for name in git(dest, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split():
        if name not in keep:
            git(dest, "branch", "-D", name)
    if "origin" in git(dest, "remote").stdout.split():
        git(dest, "remote", "remove", "origin")  # local only: nothing to push to
    git(dest, "config", "user.name", "MergeGuard Replay")
    git(dest, "config", "user.email", "replay@mergeguard.local")
    for name, sha in (("main", BASE), PR_A, PR_B):
        print(f"  {name:30s} {git(dest, 'log', '-1', '--format=%h %s', sha).stdout.strip()}")


def make_venv(dest: Path) -> None:
    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv not found: install it (https://docs.astral.sh/uv/) or pass --no-venv")
    print("uv sync --frozen (project venv for the tests) ...")
    subprocess.run([uv, "sync", "--frozen", "--quiet"], cwd=dest, check=True)


def _pytest(dest: Path, label: str, *paths: str) -> str:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
    proc = subprocess.run(
        [str(dest / ".venv" / "bin" / "python"), "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *paths],
        cwd=dest, env=env, text=True, capture_output=True,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    failed = [ln for ln in lines if ln.startswith("FAILED")]
    summary = lines[-1] if lines else proc.stderr.strip()[-300:]
    print(f"  {label:34s} exit={proc.returncode}  {summary}")
    for ln in failed:
        print(f"      {ln}")
    return summary


def prove(dest: Path) -> None:
    head = git(dest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    try:
        merge_base = git(dest, "merge-base", PR_A[1], PR_B[1]).stdout.strip()
        print(f"merge-base({PR_A[0]}, {PR_B[0]}) = {merge_base[:8]} (main = {BASE[:8]})")
        shared = set(git(dest, "diff", "--name-only", BASE, PR_A[1]).stdout.split()) & set(
            git(dest, "diff", "--name-only", BASE, PR_B[1]).stdout.split()
        )
        print(f"files changed by both PRs: {len(shared)}")
        print("pytest " + " ".join(TEST_ARGS))
        for label, sha in (("main (merge-base)", BASE), (PR_A[0], PR_A[1]), (PR_B[0], PR_B[1])):
            git(dest, "checkout", "--quiet", "--detach", sha)
            _pytest(dest, label, *TEST_ARGS)
        git(dest, "checkout", "--quiet", "--detach", PR_A[1])
        merge = git(dest, "merge", "--no-ff", "--no-commit", PR_B[1], check=False)
        print(f"git merge --no-commit {PR_B[0]} into {PR_A[0]}: exit={merge.returncode}")
        print("  " + (merge.stdout + merge.stderr).strip().replace("\n", "\n  "))
        if merge.returncode != 0:
            sys.exit("unexpected textual conflict")
        _pytest(dest, "A+B merged", *TEST_ARGS)
        fix = git(dest, "show", UPSTREAM_FIX, "--", "packages/adapters/discord/tests/test_branding.py")
        applied = subprocess.run(["git", "-C", str(dest), "apply"], input=fix.stdout, text=True)
        if applied.returncode == 0:
            _pytest(dest, f"A+B + upstream fix {UPSTREAM_FIX[:8]}", *TEST_ARGS)
    finally:
        git(dest, "merge", "--abort", check=False)
        git(dest, "reset", "--quiet", "--hard", check=False)
        git(dest, "checkout", "--quiet", "--force", head if head != "HEAD" else "main", check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--fresh", action="store_true", help="delete and re-clone")
    parser.add_argument("--no-venv", action="store_true", help="skip uv sync")
    parser.add_argument("--prove", action="store_true", help="replay the tests on A, B, A+B")
    args = parser.parse_args()
    dest = args.dest.resolve()
    build(dest, args.fresh)
    if not args.no_venv:
        make_venv(dest)
    if args.prove:
        prove(dest)
    print(f"\nreplay repo: {dest}")


if __name__ == "__main__":
    main()
