"""Thin git wrapper (subprocess) — no external deps, works on any repo."""
from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from app.schemas import BranchDiff, ChangedFile

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class GitError(RuntimeError):
    pass


def _safe_git_error(message: str, token: str | None) -> str:
    if token:
        credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        message = message.replace(credential, "[redacted]").replace(token, "[redacted]")
    return message


def _git_env(token: str | None = None) -> dict[str, str] | None:
    if not token:
        return None
    env = os.environ.copy()
    try:
        config_count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError as exc:
        raise GitError("GIT_CONFIG_COUNT must be an integer") from exc
    env["GIT_CONFIG_COUNT"] = str(config_count + 1)
    env[f"GIT_CONFIG_KEY_{config_count}"] = "http.extraheader"
    credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env[f"GIT_CONFIG_VALUE_{config_count}"] = f"AUTHORIZATION: basic {credential}"
    return env


def _run(repo: Path, *args: str, token: str | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=_git_env(token),
    )
    if proc.returncode != 0:
        error = _safe_git_error(proc.stderr.strip(), token)
        raise GitError(f"git {' '.join(args)} failed: {error}")
    return proc.stdout


def clone(repo_url: str, dest: Path, token: str | None = None) -> Path:
    if dest.exists():
        # Refresh existing clone.
        _run(dest, "fetch", "--all", "--prune", token=token)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", "--no-single-branch", repo_url, str(dest)],
        capture_output=True,
        text=True,
        env=_git_env(token),
    )
    if proc.returncode != 0:
        error = _safe_git_error(proc.stderr.strip(), token)
        raise GitError(f"clone failed: {error}")
    return dest


def fetch_pull_request(repo: Path, number: int, token: str | None = None) -> str:
    branch = f"mergeguard/pr/{number}"
    _run(
        repo,
        "fetch",
        "--no-tags",
        "origin",
        f"+refs/pull/{number}/head:refs/heads/{branch}",
        token=token,
    )
    return branch


def list_branches(repo: Path, exclude: str | None = None) -> list[str]:
    out = _run(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    branches = [b.strip() for b in out.splitlines() if b.strip()]
    if exclude:
        branches = [b for b in branches if b != exclude]
    return branches


def merge_base(repo: Path, a: str, b: str) -> str | None:
    try:
        return _run(repo, "merge-base", a, b).strip() or None
    except GitError:
        return None


def _parse_numstat(text: str) -> dict[str, tuple[int, int]]:
    """path -> (added, removed). Binary files report '-'."""
    stats: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        stats[path] = (
            int(added) if added.isdigit() else 0,
            int(removed) if removed.isdigit() else 0,
        )
    return stats


def _parse_hunks(text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse `git diff -U0` output into path -> [(start, end)] on the new side."""
    hunks: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            hunks.setdefault(current, [])
        elif line.startswith("+++ "):  # e.g. /dev/null (deletion)
            current = None
        elif line.startswith("@@") and current is not None:
            m = _HUNK_RE.match(line)
            if not m:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count == 0:
                # Pure deletion; mark the seam at `start`.
                hunks[current].append((start, start))
            else:
                hunks[current].append((start, start + count - 1))
    return hunks


def diff_against_base(repo: Path, branch: str, base: str) -> BranchDiff:
    mb = merge_base(repo, base, branch)
    ref = f"{mb}..{branch}" if mb else f"{base}..{branch}"

    numstat = _parse_numstat(_run(repo, "diff", "--numstat", ref))
    hunks = _parse_hunks(_run(repo, "diff", "-U0", "--no-color", ref))
    ahead = 0
    if mb:
        ahead = int(_run(repo, "rev-list", "--count", f"{mb}..{branch}").strip() or 0)

    files: list[ChangedFile] = []
    for path, (added, removed) in sorted(numstat.items()):
        files.append(
            ChangedFile(
                path=path,
                added_lines=added,
                removed_lines=removed,
                hunks=hunks.get(path, []),
            )
        )
    return BranchDiff(branch=branch, base=base, merge_base=mb, ahead=ahead, files=files)


def resolver_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run proposal operations without hooks, rerere, or interactive prompts."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.hooksPath=", "-c",
             "rerere.enabled=false", "-c", "core.autocrlf=false", "-c",
             "user.name=MergeGuard", "-c", "user.email=mergeguard@localhost",
             *args], capture_output=True, timeout=60,
            env={**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0",
                 "GIT_MERGE_AUTOEDIT": "no"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError("Git operation could not complete") from exc
    if check and proc.returncode:
        raise GitError(proc.stderr.decode("utf-8", errors="replace").strip())
    return proc


def resolve_commit(repo: Path, ref: str) -> str:
    return resolver_git(repo, "rev-parse", "--verify", "--end-of-options",
                        f"{ref}^{{commit}}").stdout.decode().strip()


@contextmanager
def merge_worktree(repo: Path, target: str):
    """Only this disposable worktree's checkout/index is changed."""
    with tempfile.TemporaryDirectory(prefix="mergeguard-") as temp:
        worktree = Path(temp) / "checkout"
        resolver_git(repo, "worktree", "add", "--detach", str(worktree), target)
        try:
            yield worktree
        finally:
            resolver_git(repo, "worktree", "remove", "--force", str(worktree))


def conflict_entries(repo: Path) -> dict[str, dict[int, tuple[str, str]]]:
    """Read NUL-separated stage entries, preserving spaces and unusual filenames."""
    entries: dict[str, dict[int, tuple[str, str]]] = {}
    for record in resolver_git(repo, "ls-files", "--unmerged", "-z").stdout.split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split()
        entries.setdefault(path.decode("utf-8"), {})[int(stage)] = (mode, oid)
    return entries


def conflict_blob(repo: Path, oid: str, limit: int) -> str:
    size = int(resolver_git(repo, "cat-file", "-s", oid).stdout)
    if size > limit:
        raise ValueError("File exceeds the configured context size limit")
    raw = resolver_git(repo, "cat-file", "blob", oid).stdout
    if b"\0" in raw:
        raise ValueError("Binary files require human review")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Non-UTF-8 files require human review") from exc
