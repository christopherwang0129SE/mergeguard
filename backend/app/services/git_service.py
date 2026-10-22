"""Thin git wrapper (subprocess) — no external deps, works on any repo."""
from __future__ import annotations

import base64
import os
import re
import subprocess
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


def show_file(repo: Path, ref: str, path: str) -> str | None:
    """Content of ``path`` at ``ref``, or ``None`` if it does not exist there."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def rev_parse(repo: Path, ref: str) -> str:
    return _run(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def commit_messages(repo: Path, base: str, branch: str, limit: int = 5) -> str:
    """Full messages (subject + body) of the commits on ``branch`` since ``base``."""
    mb = merge_base(repo, base, branch)
    ref = f"{mb}..{branch}" if mb else branch
    return _run(repo, "log", f"-n{max(1, limit)}", "--format=%s%n%n%b%n---", ref).strip()


def recent_commit_subjects(repo: Path, branch: str, limit: int = 5) -> list[str]:
    """Short ``hash subject`` lines for ``branch``, newest first."""
    count = max(0, limit)
    if count == 0:
        return []
    out = _run(repo, "log", f"-n{count}", "--format=%h %s", branch)
    subjects: list[str] = []
    for line in out.splitlines():
        text = " ".join(line.split())
        if not text:
            continue
        if len(text) > 120:
            text = text[:117].rstrip() + "..."
        subjects.append(text)
    return subjects


def trimmed_diff(
    repo: Path,
    branch: str,
    base: str,
    paths: list[str],
    max_chars: int,
    function_context: bool = False,
) -> str:
    """Unified diff excerpt for ``paths`` on ``branch`` vs the merge-base with ``base``.

    With ``function_context=True`` the diff is built with ``git diff -W``
    (``--function-context``): each hunk is wrapped in the whole enclosing
    function, so the model sees the unchanged context around the changed
    lines. Without it, a plain ``-U1`` unified diff is used.
    """
    if not paths or max_chars <= 0:
        return ""
    mb = merge_base(repo, base, branch)
    ref = f"{mb}..{branch}" if mb else f"{base}..{branch}"
    context_args = ["-W"] if function_context else ["-U1"]
    text = _run(repo, "diff", "--no-color", *context_args, ref, "--", *paths)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...(trimmed)\n"


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


def textual_conflict(repo: Path, a: str, b: str) -> bool | None:
    """Whether ``git merge-tree --write-tree`` reports a conflict.

    True when git exits non-zero and the output contains ``CONFLICT``.
    False when the merge exits 0. None when git fails for any other reason
    (missing ref, or a git without ``--write-tree``). Never raises.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "merge-tree", "--write-tree", a, b],
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if proc.returncode == 0:
        return False
    if "CONFLICT" in f"{proc.stdout or ''}{proc.stderr or ''}":
        return True
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
