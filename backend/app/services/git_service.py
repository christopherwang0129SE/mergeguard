"""Thin git wrapper (subprocess) — no external deps, works on any repo."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from app.schemas import BranchDiff, ChangedFile

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class GitError(RuntimeError):
    pass


def _run(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def clone(repo_url: str, dest: Path) -> Path:
    if dest.exists():
        # Refresh existing clone.
        _run(dest, "fetch", "--all", "--prune")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", "--no-single-branch", repo_url, str(dest)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(f"clone failed: {proc.stderr.strip()}")
    return dest


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
) -> str:
    """Unified diff excerpt for ``paths`` on ``branch`` vs the merge-base with ``base``."""
    if not paths or max_chars <= 0:
        return ""
    mb = merge_base(repo, base, branch)
    ref = f"{mb}..{branch}" if mb else f"{base}..{branch}"
    text = _run(repo, "diff", "--no-color", "-U1", ref, "--", *paths)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...(trimmed)\n"
