#!/usr/bin/env python3
"""Replay a mined case as local branches main, a, and b with no remote.

    python backend/scripts/bench/replay.py --cases X.jsonl --src REPO \\
        --root /workspace/mg-bench-repos/replay [--venv] [--limit N]

``main`` is ``a.parent``. Each change is applied onto ``main``: merge commits
via ``git diff <parent> <sha> | git apply --3way`` (fallback ``cherry-pick -m 1``),
squash/direct via ``git cherry-pick``. If that fails and ``pr`` is an int on an
owner repo, the branch is the fetched PR head, unless that head contains
commit A or has no diff against ``main``. Otherwise the branch is omitted.

Replays depend on the src clone via ``.git/objects/info/alternates``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path

_NAME = "MergeGuard Bench"
_EMAIL = "bench@mergeguard.local"
_AUTHOR = f"{_NAME} <{_EMAIL}>"
_OPT_GROUPS = ("test", "tests", "testing", "dev")
_PIP_FLAGS = ("--only-binary=:all:", "--disable-pip-version-check")
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_ENV = os.environ.copy()
_ENV.update(
    GIT_AUTHOR_NAME=_NAME,
    GIT_AUTHOR_EMAIL=_EMAIL,
    GIT_COMMITTER_NAME=_NAME,
    GIT_COMMITTER_EMAIL=_EMAIL,
)
_GIT_C = ("-c", f"user.name={_NAME}", "-c", f"user.email={_EMAIL}", "-c", "commit.gpgsign=false")


def _run(args: list[str], *, repo: Path | None = None, input: str | None = None, timeout: int | None = None):
    if repo is not None:
        args = ["git", "-C", str(repo), *_GIT_C, *args]
    return subprocess.run(
        args, input=input, timeout=timeout, env=_ENV, check=False, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = _run(list(args), repo=repo)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"git {args[0]} failed"
        raise RuntimeError(detail)
    return proc


def _branch(repo: Path, name: str) -> str | None:
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False)
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else None


def _hard_clean(repo: Path) -> None:
    _git(repo, "reset", "--hard", check=False)
    _git(repo, "clean", "-fd", check=False)


def _stage(repo: Path, change: dict) -> str | None:
    sha = str(change["sha"])
    if change.get("kind") == "merge":
        parent = str(change.get("parent") or "")
        diff = _git(repo, "diff", "--binary", parent, sha, check=False)
        if diff.returncode == 0 and diff.stdout.strip():
            applied = _run(["apply", "--3way", "--index", "-"], repo=repo, input=diff.stdout)
            if applied.returncode == 0:
                return "apply-3way"
            _hard_clean(repo)
        if _git(repo, "cherry-pick", "-n", "-m", "1", sha, check=False).returncode == 0:
            return "cherry-pick"
        return None
    if _git(repo, "cherry-pick", "-n", sha, check=False).returncode == 0:
        return "cherry-pick"
    return None


def _apply(repo: Path, change: dict, branch: str) -> str | None:
    sha = str(change["sha"])
    _git(repo, "checkout", "-q", "-B", branch, "main")
    method = _stage(repo, change)
    staged = method is not None and _git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0
    message = _git(repo, "log", "-1", "--format=%B", sha, check=False).stdout if staged else ""
    committed = False
    if staged:
        committed = _run(
            ["commit", "-q", "--author", _AUTHOR, "-F", "-"],
            repo=repo, input=message.strip() and message or "replay\n",
        ).returncode == 0
    if committed and method:
        return method
    _hard_clean(repo)
    _git(repo, "checkout", "-qf", "main", check=False)
    _git(repo, "branch", "-D", branch, check=False)
    return None


_METHODS = {"cherry-pick", "apply-3way", "pr-head"}


def _owner_repo(case: dict) -> bool:
    text = str(case.get("repo") or "").strip().removesuffix(".git")
    sep = "/" if "/" in text else "__" if "__" in text else ""
    owner, _, name = text.partition(sep) if sep else ("", "", "")
    return bool(owner and name)


def _pr_num(change: dict) -> int | None:
    pr = change.get("pr") if isinstance(change, dict) else None
    return pr if type(pr) is int and pr > 0 else None


def _commit_sha(repo: Path, rev: str) -> str | None:
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and sha else None


def fetch_pr_head(src_repo: Path, pr: int, *, timeout: int = 120) -> str | None:
    """Read-only fetch of ``refs/pull/<pr>/head`` into ``refs/bench/pr/<pr>``; skip if present."""
    ref = f"refs/bench/pr/{int(pr)}"
    found = _commit_sha(src_repo, ref)
    if found:
        return found
    try:
        proc = _run(
            ["fetch", "--no-tags", "origin", f"refs/pull/{int(pr)}/head:{ref}"],
            repo=src_repo, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return _commit_sha(src_repo, ref) if proc.returncode == 0 else None


def _same_as_main(repo: Path, rev: str) -> bool:
    return _git(repo, "diff", "--quiet", "main", rev, check=False).returncode == 0


def _head_contains_a(repo: Path, a_sha: object, head: str) -> bool:
    sha = a_sha.strip() if isinstance(a_sha, str) else ""
    # Unknown in this repo: skip the ancestor check rather than reject the head.
    if not sha or _commit_sha(repo, sha) is None:
        return False
    return _git(repo, "merge-base", "--is-ancestor", sha, head, check=False).returncode == 0


def _at_pr_head(
    replay: Path, src: Path, branch: str, sha: str, pr: int, *, a_sha: str | None = None,
) -> bool:
    if _commit_sha(replay, sha) is None:
        ref = f"refs/bench/pr/{pr}"
        try:
            fetched = _run(["fetch", "--no-tags", str(src), f"{ref}:{ref}"], repo=replay, timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            return False
        if fetched.returncode != 0 or _commit_sha(replay, sha) is None:
            return False
    base = _git(replay, "merge-base", "main", sha, check=False)
    if base.returncode != 0 or not base.stdout.strip():
        return False
    # A PR head that already contains A, or that matches main, is not a parallel change.
    if _head_contains_a(replay, a_sha, sha) or _same_as_main(replay, sha):
        return False
    return _git(replay, "branch", "-f", branch, sha, check=False).returncode == 0


def _place(replay: Path, src: Path, case: dict, change: dict, branch: str, fetch) -> str | None:
    method = _apply(replay, change, branch)
    pr = None if method else _pr_num(change)
    if method or pr is None or not _owner_repo(case):
        return method
    try:
        sha = str(fetch(src, pr) or "").strip()
    except (subprocess.TimeoutExpired, OSError):
        return None
    a_sha = case["a"]["sha"]
    return "pr-head" if sha and _at_pr_head(
        replay, src, branch, sha, pr, a_sha=None if a_sha is None else str(a_sha),
    ) else None


def _recall(repo: Path) -> tuple[str | None, str | None]:
    try:
        data = json.loads((repo / ".git" / "bench-methods.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data = data if isinstance(data, dict) else {}
    a, b = data.get("a_method"), data.get("b_method")
    return (a if a in _METHODS else None, b if b in _METHODS else None)


def _meta(repo: Path) -> dict:
    a, b = _branch(repo, "a"), _branch(repo, "b")
    a_method, b_method = _recall(repo)
    conflict = False
    if a and b:
        proc = _git(repo, "merge-tree", "--write-tree", "a", "b", check=False)
        conflict = proc.returncode != 0 and "CONFLICT" in proc.stdout + proc.stderr
    return {
        "path": str(repo), "main": _branch(repo, "main"), "a": a, "b": b,
        "a_apply_failed": a is None, "b_apply_failed": b is None, "textual_conflict": conflict,
        "a_method": a_method if a else None, "b_method": b_method if b else None,
        "a_empty": a is not None and _same_as_main(repo, "a"),
        "b_empty": b is not None and _same_as_main(repo, "b"),
    }


def build_replay(
    src_repo: Path,
    case: dict,
    root: Path,
    *,
    rebuild_if_b_missing: bool = True,
    fetch_pr: Callable[[Path, int], str | None] | None = None,
) -> dict:
    """Create ``root/<case_id>`` (no remote). Reuse it when ``main`` already exists.

    The replay depends on the src clone via ``.git/objects/info/alternates``.
    ``rebuild_if_b_missing`` rebuilds when ``b`` is absent. ``fetch_pr`` overrides ``fetch_pr_head``.
    """
    case_id = str(case["case_id"])
    if not case_id or case_id in {".", ".."} or "/" in case_id or "\\" in case_id:
        raise ValueError(f"unsafe case_id: {case_id!r}")
    dest = Path(root) / case_id
    if (dest / ".git").is_dir() and _branch(dest, "main"):
        if not (rebuild_if_b_missing and _branch(dest, "b") is None):
            return _meta(dest)
    if dest.exists():
        shutil.rmtree(dest)
    Path(root).mkdir(parents=True, exist_ok=True)
    clone = _run(["git", "clone", "--quiet", "--shared", "--no-checkout", str(src_repo), str(dest)])
    if clone.returncode != 0:
        raise RuntimeError((clone.stderr or clone.stdout).strip() or "git clone failed")
    for name in _git(dest, "remote").stdout.split():
        _git(dest, "remote", "remove", name)
    for key, value in (("user.name", _NAME), ("user.email", _EMAIL), ("commit.gpgsign", "false")):
        _git(dest, "config", key, value)
    _git(dest, "checkout", "-q", "-B", "main", str(case["a"]["parent"]))
    for name in _git(dest, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split():
        if name != "main":
            _git(dest, "branch", "-D", name, check=False)
    fetch = fetch_pr_head if fetch_pr is None else fetch_pr
    src = Path(src_repo)
    a_method = _place(dest, src, case, case["a"], "a", fetch)
    b_method = _place(dest, src, case, case["b"], "b", fetch)
    (dest / ".git" / "bench-methods.json").write_text(
        json.dumps({"a_method": a_method, "b_method": b_method}), encoding="utf-8",
    )
    _git(dest, "checkout", "-q", "main", check=False)
    return _meta(dest)


def _local_spec(spec: str) -> bool:
    name = spec.strip().strip("\"'")
    return name in {".", "./", "..", "../"} or name.startswith(("./", "../", "/", "file:", "file://"))


def _req_specs(text: str) -> list[str]:
    specs: list[str] = []
    buf = ""
    for raw in text.splitlines():
        piece = raw.strip()
        if piece.endswith("\\"):
            buf += piece[:-1].strip() + " "
            continue
        line, buf = (buf + piece).strip(), ""
        if not line or line.startswith("#"):
            continue
        if "#" in line and "://" not in line[: line.find("#")]:
            line = line[: line.find("#")].strip()
        if not line or line.startswith(("-r", "-c", "-f", "--", "-i")):
            continue
        if line.startswith("-e ") or line.startswith("--editable"):
            target = line.split(None, 1)[1].strip()
            if _local_spec(target):
                continue
            line = target
        line = line.split(" --hash", 1)[0].strip()
        if line and not _local_spec(line):
            specs.append(line)
    return specs


def _pyproject_specs(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    project = data.get("project") if isinstance(data, dict) else None
    if not isinstance(project, dict):
        return []
    specs: list[str] = []

    def take(items: object) -> None:
        if isinstance(items, list):
            specs.extend(
                item.strip() for item in items
                if isinstance(item, str) and item.strip() and not _local_spec(item)
            )

    take(project.get("dependencies"))
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for group in _OPT_GROUPS:
            take(optional.get(group))
    return specs


def parse_declared_deps(manifests: dict[str, str]) -> list[str]:
    """Pure parse of requirements\\*.txt and PEP 621 deps (test/tests/testing/dev only)."""
    specs: list[str] = []
    for name in sorted(manifests):
        if "/" in name or "\\" in name:
            continue
        if name.startswith("requirements") and name.endswith(".txt"):
            specs.extend(_req_specs(manifests[name]))
    pyproject = manifests.get("pyproject.toml")
    if isinstance(pyproject, str):
        specs.extend(_pyproject_specs(pyproject))
    return specs


def _dist_name(spec: str) -> str:
    base = spec.split(";", 1)[0].split("@", 1)[0].strip()
    match = _NAME_RE.match(base)
    return match.group(1).lower().replace("_", "-") if match else spec.strip().lower()


def declared_deps(replay_dir: Path) -> list[str]:
    manifests: dict[str, str] = {}
    for path in sorted(replay_dir.glob("requirements*.txt")):
        if path.is_file():
            manifests[path.name] = path.read_text(encoding="utf-8", errors="replace")
    pyproject = replay_dir / "pyproject.toml"
    if pyproject.is_file():
        manifests["pyproject.toml"] = pyproject.read_text(encoding="utf-8", errors="replace")
    return parse_declared_deps(manifests)


def _exclude_venv(repo: Path) -> None:
    git_dir = repo / ".git"
    if not git_dir.is_dir():
        return
    info = git_dir / "info"
    info.mkdir(parents=True, exist_ok=True)
    path = info / "exclude"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if any(line.strip() in {".venv", ".venv/"} for line in text.splitlines()):
        return
    path.write_text(text + ("" if text.endswith("\n") or not text else "\n") + ".venv\n", encoding="utf-8")


def _pip(pip: Path, packages: list[str], timeout: int) -> bool:
    try:
        return _run([str(pip), "install", *_PIP_FLAGS, *packages], timeout=timeout).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def make_venv(replay_dir: Path, timeout: int = 600) -> dict:
    """``python3 -m venv`` plus pytest and declared deps. Never installs the repo."""
    replay_dir = Path(replay_dir)
    _git(replay_dir, "checkout", "-q", "main", check=False)
    venv = replay_dir / ".venv"
    try:
        created = _run(["python3", "-m", "venv", str(venv)], timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "installed": [], "failed": [f"venv: {exc.__class__.__name__}"]}
    if created.returncode != 0:
        return {"ok": False, "installed": [], "failed": [(created.stderr or created.stdout).strip() or "venv failed"]}
    _exclude_venv(replay_dir)
    seen: set[str] = set()
    packages: list[str] = []
    for spec in ("pytest", *declared_deps(replay_dir)):
        key = _dist_name(spec)
        if key and key not in seen:
            seen.add(key)
            packages.append(spec)
    pip = venv / "bin" / "pip"
    if _pip(pip, packages, timeout):
        return {"ok": True, "installed": packages, "failed": []}
    installed, failed = [], []
    for package in packages:
        (installed if _pip(pip, [package], timeout) else failed).append(package)
    return {"ok": not failed, "installed": installed, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--venv", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    seen = 0
    for line in args.cases.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = build_replay(args.src, json.loads(line), args.root)
        if args.venv:
            record["venv"] = make_venv(Path(record["path"]))
        print(json.dumps(record, ensure_ascii=False))
        seen += 1
        if args.limit is not None and seen >= args.limit:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
