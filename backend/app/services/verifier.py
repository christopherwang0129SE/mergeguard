"""Verify-by-test: turn a suspected semantic conflict into evidence.

For a high-risk pair, Nemotron writes one small pytest module with
``test_branch_a_*`` tests (branch A's intent) and ``test_branch_b_*`` tests
(branch B's intent). We then run it in throwaway git worktrees:

- on branch A: ``-k branch_a`` must pass;
- on branch B: ``-k branch_b`` must pass;
- on the merge-base of A and B: all tests (to see which ones capture a
  change rather than unchanged base behaviour);
- on A merged with B (``git merge --no-commit`` in its own worktree): all
  tests.

``confirmed`` needs both own-branch runs to pass AND at least one intent test
that passes on its branch, fails on the merge-base (so it captures that
branch's change) and fails on the merge (assertion failure). A test that only
pins unchanged base behaviour can never confirm, so a docs-only branch cannot
"conflict" with a code change. Ran but missed that bar -> ``suspected``. Could not
generate or run a usable test -> ``error``.

Safety: only for local repos under the allowed roots (default: the bundled
``demo/`` folder); never for repos cloned from a URL. The test subprocess gets
a minimal environment (no API keys or other secrets), a timeout, and no
network (macOS ``sandbox-exec`` deny-network profile when available, plus an
in-process socket guard everywhere). Generated code that imports network,
process or filesystem-mutation modules is rejected before it runs.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.schemas import PairRisk, Verification, VerifyRun
from app.services import cache, git_service, nemotron
from app.services.nemotron import NemotronError
from app.services.usage import UsageMeter

PROMPT_VERSION = "verify-v1"
TEST_FILENAME = "test_mergeguard_intent.py"
MAX_OUTPUT_CHARS = 4000
MAX_FILE_CHARS = 5000
MAX_DIFF_CHARS = 3500
SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# Repo root of MergeGuard itself: backend/app/services/verifier.py -> parents[3].
DEMO_ROOT = Path(__file__).resolve().parents[3] / "demo"

_FORBIDDEN_MODULES = {
    "socket", "subprocess", "requests", "urllib", "urllib2", "urllib3", "http",
    "httpx", "aiohttp", "ftplib", "smtplib", "telnetlib", "ssl", "shutil",
    "ctypes", "multiprocessing", "asyncio", "pty", "signal", "webbrowser",
}
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint"}
_FORBIDDEN_ATTRS = {"system", "popen", "spawnl", "spawnv", "execv", "execve", "fork", "remove",
                    "unlink", "rmdir", "rmtree", "kill", "putenv", "environ", "getenv"}

SYSTEM_PROMPT = (
    "You write small, deterministic pytest tests that expose semantic merge "
    "conflicts between two git branches, A and B. Each branch changes the code "
    "with a specific intent. Write ONE self-contained pytest module that "
    "captures BOTH intents:\n"
    "- Functions named test_branch_a_<something> assert behaviour that branch A "
    "intends. They must pass on branch A's code.\n"
    "- Functions named test_branch_b_<something> assert behaviour that branch B "
    "intends. They must pass on branch B's code.\n"
    "Pick concrete inputs where the two changes interact, so that if both "
    "branches are merged the combined code would violate at least one of these "
    "intents. Do not only test helpers in isolation: call the shared entry "
    "point that both changes affect (for example the function one branch edits "
    "that calls the function the other branch edits), with inputs that trigger "
    "BOTH changes at once (exceed any new cap, limit or threshold, and depend on "
    "any new ordering). Compute every expected value by hand from THAT branch's code and "
    "show the arithmetic in a short comment. Import project modules exactly like "
    "the existing tests do; the repository root is on sys.path. Use only the "
    "standard library and pytest. No network, no subprocess, no file or "
    "environment access, no randomness, no sleeping. Define test functions at "
    "module level (no classes). Reply with only the code in a single ```python "
    "block."
)


@dataclass
class _Generated:
    code: str
    prompt_tokens: int
    completion_tokens: int


class VerifyError(Exception):
    pass


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def allowed_roots() -> list[Path]:
    roots = [DEMO_ROOT]
    for item in settings.verify_allowed_roots.split(os.pathsep):
        if item.strip():
            roots.append(Path(item.strip()).expanduser().resolve())
    return roots


def repo_allowed(repo: Path) -> bool:
    resolved = repo.resolve()
    return any(resolved == root or root in resolved.parents for root in allowed_roots())


def is_high_risk(pair: PairRisk) -> bool:
    if pair.score_source != "model":
        return False
    if pair.level == "high" or pair.model_level == "high":
        return True
    best = max(pair.score, pair.model_score or 0.0)
    return best >= settings.verify_min_score


# ---------------------------------------------------------------------------
# Prompt + code extraction / validation
# ---------------------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n...(trimmed)"


def _changed_py(repo: Path, base: str, branch: str) -> list[str]:
    diff = git_service.diff_against_base(repo, branch, base)
    return [f.path for f in diff.files if f.path.endswith(".py")]


def _existing_test_example(repo: Path, base: str) -> str:
    out = git_service._run(repo, "ls-tree", "-r", "--name-only", base)
    for path in out.splitlines():
        name = Path(path).name
        if path.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")):
            text = git_service.show_file(repo, base, path) or ""
            return f"# {path}\n{_clip(text, 2000)}"
    return "(no existing tests)"


def build_test_prompt(repo: Path, base: str, branch_a: str, branch_b: str) -> str:
    py_a = _changed_py(repo, base, branch_a)
    py_b = _changed_py(repo, base, branch_b)
    source_files = sorted({p for p in py_a + py_b if "test" not in Path(p).name})
    sections: list[str] = [
        f"Base branch: {base}",
        f"Branch A: {branch_a}",
        f"Branch B: {branch_b}",
        "",
    ]
    for label, branch, paths in (("A", branch_a, py_a), ("B", branch_b, py_b)):
        sections.append(f"== Branch {label} ({branch}) commit messages ==")
        sections.append(_clip(git_service.commit_messages(repo, base, branch), 1500))
        sections.append(f"== Branch {label} ({branch}) diff vs {base} (function context) ==")
        diff = git_service.trimmed_diff(
            repo, branch, base, paths, max_chars=MAX_DIFF_CHARS, function_context=True
        )
        sections.append(diff.strip() or "(no Python changes)")
        sections.append("")
    for path in source_files[:3]:
        for label, ref in (("base", base), ("A", branch_a), ("B", branch_b)):
            text = git_service.show_file(repo, ref, path)
            if text is not None:
                sections.append(f"== {path} on {label} ({ref}) ==")
                sections.append(_clip(text, MAX_FILE_CHARS))
    sections.append("== Example of an existing test (import style) ==")
    sections.append(_existing_test_example(repo, base))
    return "\n".join(sections)


def build_repair_prompt(original: str, code: str, failures: list[VerifyRun], problem: str) -> str:
    lines = [original, "", "== Your previous test ==", "```python", code, "```", "", problem]
    for run in failures:
        lines.append(f"-- result on {run.target} ({run.ref}), -k {run.selection} --")
        lines.append(_clip(run.output, 1500))
    lines.append(
        "Fix the test so every test_branch_a_* passes on branch A and every "
        "test_branch_b_* passes on branch B. Recompute expected values from each "
        "branch's own code. Reply with only the full corrected module in one "
        "```python block."
    )
    return "\n".join(lines)


def extract_code(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", cleaned, flags=re.DOTALL)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    return cleaned.strip() + "\n"


def validate_code(code: str) -> str | None:
    """Return a problem description, or ``None`` if the test may run."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"generated test is not valid Python: {exc.msg} (line {exc.lineno})"
    names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not any(n.startswith("test_branch_a") for n in names):
        return "generated test has no module-level test_branch_a_* function"
    if not any(n.startswith("test_branch_b") for n in names):
        return "generated test has no module-level test_branch_b_* function"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_MODULES:
                    return f"generated test imports forbidden module {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _FORBIDDEN_MODULES:
                return f"generated test imports forbidden module {node.module}"
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALLS:
                return f"generated test calls forbidden builtin {func.id}()"
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            return f"generated test uses forbidden attribute .{node.attr}"
    return None


# ---------------------------------------------------------------------------
# Sandboxed execution
# ---------------------------------------------------------------------------

_RUNNER = '''import socket
import sys


def _no_network(*_args, **_kwargs):
    raise OSError("network disabled by the MergeGuard verify sandbox")


socket.socket.connect = _no_network
socket.socket.connect_ex = _no_network
socket.create_connection = _no_network
socket.getaddrinfo = _no_network

import pytest  # noqa: E402

sys.exit(pytest.main(sys.argv[1:]))
'''


def sandbox_kind() -> str:
    if sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC):
        return "sandbox-exec(deny network, writes only in tmp) + socket guard"
    return "socket guard"


def _sandbox_prefix(tmp_root: Path) -> list[str]:
    if sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC):
        real = os.path.realpath(tmp_root)
        profile = (
            "(version 1)(allow default)(deny network*)"
            "(deny file-write*)"
            f'(allow file-write* (subpath "{real}"))'
            '(allow file-write* (subpath "/dev"))'
        )
        return [SANDBOX_EXEC, "-p", profile]
    return []


def clean_env(home: Path) -> dict[str, str]:
    """Minimal environment for the test subprocess. No secrets pass through."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "NO_PROXY": "*",
    }


def _junit_cases(path: Path) -> dict[str, str]:
    """Per test function outcome from a pytest junit XML report."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}
    cases: dict[str, str] = {}
    for case in root.iter("testcase"):
        name = case.get("name", "")
        if not name:
            continue
        if case.find("failure") is not None:
            cases[name] = "failed"
        elif case.find("error") is not None:
            cases[name] = "error"
        elif case.find("skipped") is not None:
            cases[name] = "skipped"
        else:
            cases[name] = "passed"
    return cases


def _junit_counts(path: Path) -> tuple[int, int, int]:
    """(passed, failed, errors) from a pytest junit XML report."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return (0, 0, 0)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = failures = errors = skipped = 0
    for suite in suites:
        tests += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
    return (max(tests - failures - errors - skipped, 0), failures, errors)


def run_test(
    workdir: Path,
    tmp_root: Path,
    *,
    target: str,
    ref: str,
    selection: str,
    timeout: float,
) -> VerifyRun:
    """Run the generated test in ``workdir`` with ``-k selection``."""
    runner = tmp_root / "mg_runner.py"
    if not runner.exists():
        runner.write_text(_RUNNER, encoding="utf-8")
    report = tmp_root / f"report-{target}.xml"
    home = tmp_root / "home"
    home.mkdir(exist_ok=True)
    cmd = [
        *_sandbox_prefix(tmp_root),
        sys.executable,
        "-I",
        str(runner),
        "-q",
        "-p",
        "no:cacheprovider",
        "--noconftest",
        "-c",
        os.devnull,
        f"--rootdir={workdir}",
        f"--junitxml={report}",
        "-k",
        selection,
        str(workdir / TEST_FILENAME),
    ]
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=workdir,
        env=clean_env(home),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = proc.communicate()
    duration = round(time.monotonic() - started, 2)
    text = (out or b"").decode("utf-8", errors="replace")
    text = text.replace(os.path.realpath(tmp_root), "<tmp>").replace(str(tmp_root), "<tmp>")
    text = re.sub(r"(?:\.\./)+<tmp>", "<tmp>", text)
    passed, failed, errors = _junit_counts(report)
    return VerifyRun(
        target=target,  # type: ignore[arg-type]
        ref=ref,
        selection=selection,
        exit_code=None if timed_out else proc.returncode,
        passed=passed,
        failed=failed,
        errors=errors,
        timed_out=timed_out,
        duration_s=duration,
        output=_clip(text, MAX_OUTPUT_CHARS),
        cases=_junit_cases(report),
    )


def _run_ok(run: VerifyRun) -> bool:
    return (not run.timed_out) and run.exit_code == 0 and run.passed > 0 and run.failed == 0 and run.errors == 0


STALE_WORKTREE_SECONDS = 30 * 60


def cleanup_stale_worktrees(repo: Path, max_age_s: float = STALE_WORKTREE_SECONDS) -> int:
    """Remove ``mg-verify-*`` worktrees left behind by a killed run.

    Only touches worktrees this module created (temp dirs named
    ``mg-verify-*``) that are older than ``max_age_s``, so concurrent runs are
    never disturbed. Returns how many worktrees were removed.
    """
    try:
        listing = git_service._run(repo, "worktree", "list", "--porcelain")
    except git_service.GitError:
        return 0
    removed = 0
    parents: set[Path] = set()
    now = time.time()
    for line in listing.splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line[len("worktree "):])
        if not path.parent.name.startswith("mg-verify-"):
            continue
        try:
            age = now - path.parent.stat().st_mtime
        except OSError:
            age = max_age_s + 1  # directory is gone: always stale
        if age <= max_age_s:
            continue
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(path)],
            capture_output=True,
        )
        parents.add(path.parent)
        removed += 1
    for parent in parents:
        # Our temp root holds only the worktrees plus runner/report files.
        if parent.is_dir() and not any(p.is_dir() and (p / ".git").exists() for p in parent.iterdir()):
            shutil.rmtree(parent, ignore_errors=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)
    return removed


class _Worktrees:
    """Throwaway worktrees for A, B and the merge of A+B. Always cleaned up."""

    def __init__(self, repo: Path, sha_a: str, sha_b: str, sha_base: str) -> None:
        self.repo = repo
        self.sha_a = sha_a
        self.sha_b = sha_b
        self.sha_base = sha_base
        self.tmp = Path(tempfile.mkdtemp(prefix="mg-verify-"))
        self.paths: dict[str, Path] = {}
        self.merge_conflict = False
        self.merge_output = ""

    def __enter__(self) -> "_Worktrees":
        cleanup_stale_worktrees(self.repo)
        try:
            for name, sha in (
                ("a", self.sha_a),
                ("b", self.sha_b),
                ("base", self.sha_base),
                ("merge", self.sha_a),
            ):
                path = self.tmp / name
                git_service._run(self.repo, "worktree", "add", "--detach", "--quiet", str(path), sha)
                self.paths[name] = path
            proc = subprocess.run(
                [
                    "git", "-C", str(self.paths["merge"]),
                    "-c", "user.name=MergeGuard Verify", "-c", "user.email=verify@mergeguard.local",
                    "merge", "--no-commit", "--no-ff", self.sha_b,
                ],
                capture_output=True,
                text=True,
            )
            self.merge_output = (proc.stdout + proc.stderr).strip()
            self.merge_conflict = proc.returncode != 0
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_exc) -> None:
        for path in self.paths.values():
            subprocess.run(
                ["git", "-C", str(self.repo), "worktree", "remove", "--force", str(path)],
                capture_output=True,
            )
        shutil.rmtree(self.tmp, ignore_errors=True)
        subprocess.run(["git", "-C", str(self.repo), "worktree", "prune"], capture_output=True)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


# Reasoning budget for test generation. If the model still runs out of tokens
# while reasoning, the next attempt turns reasoning off (fast, and the repair
# round feeds back real pytest output anyway).
VERIFY_MAX_TOKENS = 16384
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}


def _generate(prompt: str, *, thinking: bool = True) -> _Generated:
    reply = nemotron.complete(
        prompt,
        system_prompt=SYSTEM_PROMPT,
        max_tokens=VERIFY_MAX_TOKENS,
        timeout=settings.verify_llm_timeout_seconds,
        extra_body=None if thinking else NO_THINKING,
    )
    prompt_tokens, completion_tokens = nemotron.usage_of(reply)
    return _Generated(extract_code(reply), prompt_tokens, completion_tokens)


def confirming_tests(runs: list[VerifyRun]) -> list[str]:
    """Intent tests that pass on their branch, fail on the base and fail on the merge."""
    by_target = {r.target: r for r in runs}
    a, b, base, merge = (by_target.get(k) for k in ("a", "b", "base", "merge"))
    if not (a and b and base and merge) or merge.timed_out:
        return []
    out: list[str] = []
    for name, outcome in sorted(merge.cases.items()):
        if outcome != "failed":
            continue
        own = a if name.startswith("test_branch_a") else b if name.startswith("test_branch_b") else None
        if own is None or own.cases.get(name) != "passed":
            continue
        if base.cases.get(name) == "passed":
            continue  # pins unchanged base behaviour; not evidence of a conflict
        out.append(name)
    return out


def _run_all(wt: "_Worktrees", pair: PairRisk, code: str) -> list[VerifyRun]:
    """A's intent on A, B's on B, all on the merge-base and on the merge."""
    timeout = settings.verify_timeout_seconds
    for name in ("a", "b", "base", "merge"):
        (wt.paths[name] / TEST_FILENAME).write_text(code, encoding="utf-8")
    runs = [
        run_test(wt.paths["a"], wt.tmp, target="a", ref=pair.branch_a,
                 selection="branch_a", timeout=timeout),
        run_test(wt.paths["b"], wt.tmp, target="b", ref=pair.branch_b,
                 selection="branch_b", timeout=timeout),
        run_test(wt.paths["base"], wt.tmp, target="base", ref=f"merge-base {wt.sha_base[:7]}",
                 selection="branch", timeout=timeout),
    ]
    if not wt.merge_conflict:
        runs.append(
            run_test(wt.paths["merge"], wt.tmp, target="merge",
                     ref=f"{pair.branch_a}+{pair.branch_b}", selection="branch", timeout=timeout)
        )
    return runs


def _artifact_dir(key: str) -> Path:
    path = Path(settings.workspace_dir).resolve() / "verify" / key[:16]
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(repo: Path, base: str, sha_a: str, sha_b: str) -> str:
    return cache.cache_key(
        base_sha=git_service.rev_parse(repo, base),
        head_a=sha_a,
        head_b=sha_b,
        model=settings.nebius_model,
        prompt_version=PROMPT_VERSION,
    )


def _load_cached_test(key: str) -> str | None:
    path = Path(settings.workspace_dir).resolve() / "verify-cache" / f"{key}.py"
    return path.read_text(encoding="utf-8") if path.exists() else None


def _store_cached_test(key: str, code: str) -> None:
    root = Path(settings.workspace_dir).resolve() / "verify-cache"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{key}.py").write_text(code, encoding="utf-8")


def verify_pair(
    repo: Path,
    base: str,
    pair: PairRisk,
    *,
    meter: UsageMeter | None = None,
    use_cache: bool = True,
) -> Verification:
    meter = meter or UsageMeter()
    started = time.monotonic()
    tokens = [0, 0]

    def finish(status: str, reason: str, **extra) -> Verification:
        result = Verification(
            status=status,  # type: ignore[arg-type]
            reason=reason,
            sandbox=sandbox_kind(),
            duration_s=round(time.monotonic() - started, 2),
            prompt_tokens=tokens[0],
            completion_tokens=tokens[1],
            **extra,
        )
        return result

    try:
        sha_a = git_service.rev_parse(repo, pair.branch_a)
        sha_b = git_service.rev_parse(repo, pair.branch_b)
        if not (_changed_py(repo, base, pair.branch_a) or _changed_py(repo, base, pair.branch_b)):
            return finish("skipped", "no Python changes on either branch")
        key = _cache_key(repo, base, sha_a, sha_b)
        prompt = build_test_prompt(repo, base, pair.branch_a, pair.branch_b)
    except git_service.GitError as exc:
        return finish("error", f"git: {exc}"[:300])

    code: str | None = _load_cached_test(key) if use_cache else None
    cached_test = code is not None
    if cached_test:
        meter.record_cache_hit()
    attempts = 0
    runs: list[VerifyRun] = []
    last_code: str | None = None
    thinking = True
    max_attempts = max(1, int(settings.verify_max_attempts))

    try:
        sha_base = git_service.merge_base(repo, sha_a, sha_b) or git_service.rev_parse(repo, base)
        with _Worktrees(repo, sha_a, sha_b, sha_base) as wt:
            next_prompt = prompt
            while True:
                if code is None:
                    if attempts >= max_attempts:
                        break
                    attempts += 1
                    try:
                        gen = _generate(next_prompt, thinking=thinking)
                    except NemotronError as exc:
                        meter.record_call(exc.prompt_tokens, exc.completion_tokens)
                        tokens[0] += exc.prompt_tokens
                        tokens[1] += exc.completion_tokens
                        if "finish_reason=length" in exc.detail:
                            thinking = False  # reasoning ate the budget: answer directly
                        if attempts < max_attempts:
                            continue  # retry (without reasoning if it ran out of tokens)
                        if last_code is None:
                            return finish("error", f"model: {exc.detail or exc.code}",
                                          attempts=attempts)
                        break
                    meter.record_call(gen.prompt_tokens, gen.completion_tokens)
                    tokens[0] += gen.prompt_tokens
                    tokens[1] += gen.completion_tokens
                    code = gen.code
                problem = validate_code(code)
                if problem:
                    if cached_test or attempts >= max_attempts:
                        if last_code is not None:
                            break  # keep the last test that did run
                        return finish("error", problem, test_code=code, attempts=attempts,
                                      cached_test=cached_test)
                    next_prompt = build_repair_prompt(prompt, code, [], problem)
                    code = None
                    continue
                runs = _run_all(wt, pair, code)
                last_code = code
                own_ok = _run_ok(runs[0]) and _run_ok(runs[1])
                if (own_ok and (confirming_tests(runs) or wt.merge_conflict)) or cached_test:
                    break
                if attempts >= max_attempts:
                    break
                if not own_ok:
                    failing = [r for r in runs[:2] if not _run_ok(r)]
                    next_prompt = build_repair_prompt(
                        prompt, code, failing, "The previous test did not pass on its own branch."
                    )
                else:
                    next_prompt = build_repair_prompt(
                        prompt,
                        code,
                        runs[2:],
                        "The previous test passes on each branch, but no test that captures a "
                        "branch's change (fails on the base) fails on the merge of A and B, so "
                        "it does not exercise the interaction. Choose inputs and the shared "
                        "entry point so both changes take effect together.",
                    )
                code = None

            code = last_code
            if code is None:
                return finish("error", "no usable test generated", attempts=attempts)
            _store_cached_test(key, code)

            own_ok = len(runs) >= 2 and _run_ok(runs[0]) and _run_ok(runs[1])
            run_m = next((r for r in runs if r.target == "merge"), None)
            confirming = confirming_tests(runs)
            if not own_ok:
                bad = [r.target.upper() for r in runs[:2] if not _run_ok(r)]
                status = "suspected"
                reason = f"generated test does not pass on branch {' and '.join(bad)}"
            elif wt.merge_conflict or run_m is None:
                status, reason = "suspected", "textual merge conflict: git merge --no-commit failed"
            elif run_m.timed_out:
                status, reason = "error", "test timed out on the merge"
            elif confirming:
                status = "confirmed"
                reason = (
                    f"intent tests pass on A and on B; {len(confirming)} that capture a "
                    f"branch's change fail on the merge of A+B"
                )
            elif run_m.failed > 0:
                status = "suspected"
                reason = "merge only fails tests that pin unchanged base behaviour"
            elif run_m.errors > 0:
                status, reason = "suspected", "test errored on the merge (not an assertion failure)"
            else:
                status, reason = "suspected", "generated test also passes on the merge"

            art = _artifact_dir(key)
            (art / TEST_FILENAME).write_text(code, encoding="utf-8")
            result = finish(
                status, reason, test_code=code, test_path=str(art / TEST_FILENAME), runs=runs,
                attempts=attempts, cached_test=cached_test, merge_conflict=wt.merge_conflict,
                confirming_tests=confirming,
            )
            (art / "result.json").write_text(
                json.dumps(result.model_dump(), indent=2) + "\n", encoding="utf-8"
            )
            return result
    except git_service.GitError as exc:
        return finish("error", f"git: {exc}"[:300], attempts=attempts)
    except OSError as exc:
        return finish("error", f"runner: {exc}"[:300], attempts=attempts)
