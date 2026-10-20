"""Thin git wrapper (subprocess) — no external deps, works on any repo."""
from __future__ import annotations

import base64
import os
import re
import subprocess
from pathlib import Path

from app.schemas import (
    BranchDiff,
    ChangedFile,
    DiffHunk,
    DiffLine,
    FileSide,
    PairDiffFile,
    PairDiffResponse,
)

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class GitError(RuntimeError):
    pass


def redact_secrets(message: str, *secrets: str | None) -> str:
    """Drop credentials from a string that may be shown to a caller."""
    for secret in secrets:
        if secret and len(secret) >= 8:
            message = message.replace(secret, "[redacted]")
    message = re.sub(r"(https?://)[^/\s@]+:[^/\s@]+@", r"\1", message)
    message = re.sub(r"\bx-access-token:[^\s\"']+", "x-access-token:[redacted]", message)
    return message


def strip_userinfo(url: str) -> str:
    """Remove ``user:pass@`` from an http(s) URL before it is logged or passed to git."""
    return re.sub(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@", r"\1", url, count=1)


def _safe_git_error(message: str, token: str | None) -> str:
    if token:
        credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        message = message.replace(credential, "[redacted]").replace(token, "[redacted]")
    return redact_secrets(message, token)


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
    repo_url = strip_userinfo(repo_url)
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


_LOCAL_BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]+")


def fetch_pull_request(
    repo: Path,
    number: int,
    token: str | None = None,
    *,
    local_branch: str | None = None,
) -> str:
    """Fetch ``refs/pull/<number>/head`` into a local branch.

    The GitHub sync route keeps the historical name ``mergeguard/pr/<number>``.
    Pass ``local_branch`` (for example ``pr-12``) to store it under another name.
    """
    if local_branch is None:
        branch = f"mergeguard/pr/{number}"
    else:
        parts = local_branch.split("/")
        if (
            not _LOCAL_BRANCH_RE.fullmatch(local_branch)
            or local_branch.startswith(("-", "/"))
            or local_branch.endswith("/")
            or ".." in parts
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise GitError(f"invalid local branch name {local_branch!r}")
        branch = local_branch
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


def list_remote_branches(repo: Path, exclude: str | None = None) -> list[str]:
    """Remote-tracking branches (``origin/feat``), skipping ``origin/HEAD``.

    A fresh clone only checks out the default branch, so URL analyses use this
    list. ``exclude`` may be ``main`` or ``origin/main``; both drop the base.
    """
    # Full ref names: %(refname:short) turns refs/remotes/origin/HEAD into
    # "origin", which is not a branch.
    out = _run(repo, "for-each-ref", "--format=%(refname)", "refs/remotes")
    skip_full: set[str] = set()
    skip_short: set[str] = set()
    if exclude:
        skip_full.add(exclude)
        if "/" in exclude:
            skip_short.add(exclude.split("/", 1)[1])
        else:
            skip_short.add(exclude)
    branches: list[str] = []
    prefix = "refs/remotes/"
    for line in out.splitlines():
        ref = line.strip()
        if not ref.startswith(prefix):
            continue
        name = ref[len(prefix) :]
        if not name or "/" not in name or name.endswith("/HEAD"):
            continue
        short = name.split("/", 1)[1]
        if name in skip_full or short in skip_short:
            continue
        branches.append(name)
    return branches


def ref_exists(repo: Path, ref: str) -> bool:
    try:
        rev_parse(repo, ref)
        return True
    except GitError:
        return False


def prefer_ref(repo: Path, name: str) -> str:
    """Use ``name`` when it resolves, otherwise ``origin/<name>``."""
    if ref_exists(repo, name):
        return name
    remote = name if name.startswith("origin/") else f"origin/{name}"
    if remote != name and ref_exists(repo, remote):
        return remote
    return name


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


# Same adjacency window Sentinel uses when it decides a file's hunks overlap.
_PAIR_ADJACENCY = 3
# The patch string is capped. Structured hunks stay complete so the UI can still align them.
PATCH_MAX_CHARS = 200_000

_STATUS_BY_CODE = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "T": "modified",
    "C": "added",
    "R": "modified",
}
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _check_rev(name: str) -> str:
    if not name or name.startswith("-") or "\x00" in name or name.strip() != name:
        raise GitError(f"invalid revision {name!r}")
    return name


def _ranges_collide(a: tuple[int, int], b: tuple[int, int], pad: int = _PAIR_ADJACENCY) -> bool:
    return a[0] - pad <= b[1] and b[0] - pad <= a[1]


def _name_status(repo: Path, left: str, right: str) -> dict[str, str]:
    """Path to modified/added/deleted between two commits. Renames are off."""
    out = _run(repo, "diff", "--name-status", "-z", "--no-renames", left, right)
    parts = out.split("\0")
    found: dict[str, str] = {}
    index = 0
    while index + 1 < len(parts):
        code, path = parts[index], parts[index + 1]
        index += 2
        if not code or not path:
            continue
        found[path] = _STATUS_BY_CODE.get(code[:1], "modified")
    return found


def _is_binary_patch(patch: str) -> bool:
    if "@@ " in patch or patch.startswith("@@"):
        return False
    return "Binary files " in patch or "GIT binary patch" in patch


def _parse_unified_diff(patch: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    header: dict[str, int] | None = None
    lines: list[DiffLine] = []
    old_no = 0
    new_no = 0

    def finish() -> None:
        nonlocal header, lines
        if header is None:
            return
        hunks.append(
            DiffHunk(
                old_start=header["old_start"],
                old_lines=header["old_lines"],
                new_start=header["new_start"],
                new_lines=header["new_lines"],
                lines=lines,
            )
        )
        header = None
        lines = []

    for raw in patch.splitlines():
        if raw.startswith("@@"):
            finish()
            match = _HUNK_HEADER.match(raw)
            if not match:
                header = None
                continue
            header = {
                "old_start": int(match.group(1)),
                "old_lines": int(match.group(2)) if match.group(2) is not None else 1,
                "new_start": int(match.group(3)),
                "new_lines": int(match.group(4)) if match.group(4) is not None else 1,
            }
            lines = []
            old_no = header["old_start"]
            new_no = header["new_start"]
            continue
        if header is None or raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            lines.append(DiffLine(kind="add", old_no=None, new_no=new_no, text=raw[1:]))
            new_no += 1
        elif raw.startswith("-"):
            lines.append(DiffLine(kind="del", old_no=old_no, new_no=None, text=raw[1:]))
            old_no += 1
        elif raw.startswith(" "):
            lines.append(DiffLine(kind="context", old_no=old_no, new_no=new_no, text=raw[1:]))
            old_no += 1
            new_no += 1
    finish()
    return hunks


def _unchanged_side() -> FileSide:
    return FileSide(status="unchanged", patch="", hunks=[], truncated=False)


def _file_side(repo: Path, left: str, right: str, path: str, status: str, context: int) -> FileSide:
    patch = _run(
        repo,
        "diff",
        f"-U{context}",
        "--no-color",
        "--no-ext-diff",
        left,
        right,
        "--",
        path,
    )
    if _is_binary_patch(patch):
        return FileSide(status=status, patch="", hunks=[], truncated=False)  # type: ignore[arg-type]
    hunks = _parse_unified_diff(patch)
    truncated = len(patch) > PATCH_MAX_CHARS
    shown = patch[:PATCH_MAX_CHARS] if truncated else patch
    return FileSide(status=status, patch=shown, hunks=hunks, truncated=truncated)  # type: ignore[arg-type]


def _change_ranges(repo: Path, left: str, right: str, path: str) -> list[tuple[int, int]]:
    """New-side changed ranges from a zero-context diff, matching Sentinel."""
    text = _run(repo, "diff", "-U0", "--no-color", "--no-ext-diff", left, right, "--", path)
    return _parse_hunks(text).get(path, [])


def pair_diff(
    repo: Path,
    base: str,
    branch_a: str,
    branch_b: str,
    context: int = 3,
) -> PairDiffResponse:
    """Side-by-side diffs of ``branch_a`` and ``branch_b`` against ``base``.

    Shared paths come first (overlapping regions before disjoint ones), then
    files only one branch touches. A file neither branch changes is omitted.
    """
    base = _check_rev(base)
    branch_a = _check_rev(branch_a)
    branch_b = _check_rev(branch_b)
    context = max(0, min(int(context), 100))
    merge_base_a = merge_base(repo, base, branch_a)
    merge_base_b = merge_base(repo, base, branch_b)
    if not merge_base_a:
        raise GitError(f"No merge-base between {base!r} and {branch_a!r}")
    if not merge_base_b:
        raise GitError(f"No merge-base between {base!r} and {branch_b!r}")

    status_a = _name_status(repo, merge_base_a, branch_a)
    status_b = _name_status(repo, merge_base_b, branch_b)
    shared = sorted(set(status_a) & set(status_b))
    only_a = sorted(set(status_a) - set(status_b))
    only_b = sorted(set(status_b) - set(status_a))

    ranges_a = {path: _change_ranges(repo, merge_base_a, branch_a, path) for path in shared}
    ranges_b = {path: _change_ranges(repo, merge_base_b, branch_b, path) for path in shared}
    overlapping = {
        path: _ranges_collide_any(ranges_a[path], ranges_b[path]) for path in shared
    }
    shared.sort(key=lambda path: (not overlapping[path], path))

    files: list[PairDiffFile] = []
    for path in shared + only_a + only_b:
        side_a = (
            _file_side(repo, merge_base_a, branch_a, path, status_a[path], context)
            if path in status_a
            else _unchanged_side()
        )
        side_b = (
            _file_side(repo, merge_base_b, branch_b, path, status_b[path], context)
            if path in status_b
            else _unchanged_side()
        )
        files.append(
            PairDiffFile(
                path=path,
                shared=path in status_a and path in status_b,
                overlapping=overlapping.get(path, False),
                a=side_a,
                b=side_b,
            )
        )
    return PairDiffResponse(
        base_branch=base,
        branch_a=branch_a,
        branch_b=branch_b,
        merge_base_a=merge_base_a,
        merge_base_b=merge_base_b,
        files=files,
    )


def _ranges_collide_any(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    return any(_ranges_collide(left, right) for left in a for right in b)
