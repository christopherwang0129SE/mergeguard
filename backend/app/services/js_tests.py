"""JS/TS support for the branch-tests stage: vitest and jest.

Kept cheap and inside the same safety rules as the pytest runs:

- **Dependencies, once per lockfile.** The repo's manifests (every
  ``package.json`` plus the lockfile and package-manager config, nothing else)
  are copied into ``WORKSPACE_DIR/js-deps/<repo>/<manager>-<lock fingerprint>``
  and installed there with lifecycle scripts disabled (``npm ci
  --ignore-scripts`` / ``pnpm install --frozen-lockfile --ignore-scripts`` /
  ``yarn install --frozen-lockfile --ignore-scripts``). This is the only step
  with network access, it runs outside the test sandbox with a stripped
  environment, only for repos under the allowed roots, and is reused by every
  later run with the same lockfile. For npm the fingerprint ignores the
  workspace packages' own version fields, so a release bump does not force a
  second install.
- **Linking, per worktree.** Each worktree gets real ``node_modules``
  directories whose entries are symlinks into the prepared install, except
  workspace-package links, which point at the worktree's own packages. Node
  resolves real paths, so third-party code comes from the cache and first-party
  code from the branch under test.
- **Builds, only what the tests import.** A workspace package that the test's
  package depends on (transitively) and whose ``main``/``exports`` entry is a
  missing build output (``dist/...``) is built in the worktree with its own
  ``build`` script, inside the sandbox, under a time cap. The output is cached
  by the git tree hash of that package's directory, so an unchanged package is
  built once and then copied (copy-on-write on APFS).
- **Tests.** ``vitest run --reporter=json`` / ``jest --json`` on the changed
  test files, per package, inside the sandbox (``sandbox-exec`` on macOS: no
  network beyond localhost, writes only in the temp dir), with the stripped
  environment, a ``--require`` network guard (no TCP/DNS beyond loopback) and
  the per-run timeout.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.services import git_service, verifier

JS_TEST = re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs|mts|cts)$")
LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
)
MANIFEST_NAMES = {
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml",
    "pnpm-workspace.yaml", "yarn.lock", ".npmrc", ".yarnrc", ".yarnrc.yml", ".pnpmfile.cjs",
}
_SKIP_ENTRIES = {".cache", ".vite", ".vitest", ".package-lock.json"}
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
EXCERPT_CHARS = 300

NET_GUARD = r"""'use strict';
// MergeGuard branch-test network guard: no TCP or DNS beyond loopback.
const net = require('net');
const dns = require('dns');
const LOCAL = new Set(['localhost', '127.0.0.1', '::1', '0.0.0.0', '']);
function blocked(host) { throw new Error('network disabled by the MergeGuard verify sandbox (' + host + ')'); }
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  let opts = args[0];
  if (Array.isArray(opts)) opts = opts[0];
  if (opts && typeof opts === 'object') {
    if (opts.path || LOCAL.has(String(opts.host || 'localhost'))) return connect.apply(this, args);
    return blocked(opts.host);
  }
  if (typeof opts === 'string' && !/^\d+$/.test(opts)) return connect.apply(this, args);  // IPC path
  const host = typeof args[1] === 'string' ? args[1] : 'localhost';
  if (LOCAL.has(host)) return connect.apply(this, args);
  return blocked(host);
};
for (const name of ['lookup', 'resolve', 'resolve4', 'resolve6']) {
  const orig = dns[name];
  dns[name] = function (host, ...rest) { return LOCAL.has(String(host)) ? orig.call(this, host, ...rest) : blocked(host); };
  if (dns.promises && dns.promises[name]) {
    const porig = dns.promises[name];
    dns.promises[name] = async (host, ...rest) => (LOCAL.has(String(host)) ? porig(host, ...rest) : blocked(host));
  }
}
"""


class JsSetupError(Exception):
    """Dependencies could not be prepared (no node, no lockfile, install failed)."""


def is_js_test(path: str) -> bool:
    return bool(JS_TEST.search(Path(path).name)) and "node_modules/" not in path


def node_binary() -> str | None:
    if settings.branch_tests_node:
        return settings.branch_tests_node if os.path.exists(settings.branch_tests_node) else None
    search = os.pathsep.join([
        os.environ.get("PATH", ""), str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
    ])
    found = shutil.which("node", path=search)
    return os.path.realpath(found) if found else None


def find_tool(name: str, node: str) -> str | None:
    """npm / pnpm / yarn: next to node first, then the usual install places."""
    search = os.pathsep.join([
        str(Path(node).parent), "/opt/homebrew/bin", "/usr/local/bin",
        str(Path.home() / ".local" / "bin"), "/usr/bin",
    ])
    return shutil.which(name, path=search)


def tool_env(home: Path, node: str, extra: str | None = None) -> dict[str, str]:
    """The stripped test environment plus node's own bin dir (and the package
    manager's, ``extra``) on PATH."""
    home.mkdir(parents=True, exist_ok=True)
    env = verifier.clean_env(home)
    dirs = [str(Path(node).parent)] + ([str(Path(extra).parent)] if extra else [])
    env.update({
        "PATH": ":".join(dict.fromkeys([*dirs, "/usr/bin", "/bin"])),
        "CI": "1",
        "NO_COLOR": "1",
        "FORCE_COLOR": "0",
        "npm_config_update_notifier": "false",
        "npm_config_fund": "false",
        "npm_config_audit": "false",
    })
    return env


def package_manager(root: Path) -> tuple[str, str] | None:
    for lock, manager in LOCKFILES:
        if (root / lock).is_file():
            return manager, lock
    return None


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _dep_names(pkg: dict) -> set[str]:
    out: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = pkg.get(key)
        if isinstance(section, dict):
            out |= set(section)
    return out


def _declares(pkg_dir: Path, pkg: dict, runner: str) -> bool:
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    test_script = str(scripts.get("test", ""))
    return (
        runner in _dep_names(pkg)
        or any(pkg_dir.glob(f"{runner}.config.*"))
        or re.search(rf"\b{runner}\b", test_script) is not None
        or (runner == "jest" and "jest" in pkg)
    )


def runner_for(root: Path, test_path: str) -> tuple[str, str] | None:
    """(runner, package dir relative to ``root``) for a test file: the nearest
    ``package.json`` at or above the file that declares vitest or jest (as a
    dependency, a script or a config file); None when there is none."""
    root = root.resolve()
    d = (root / test_path).parent
    while True:
        pj = d / "package.json"
        if pj.is_file():
            pkg = _read_json(pj)
            for runner in ("vitest", "jest"):
                if _declares(d, pkg, runner):
                    return runner, str(d.relative_to(root)) if d != root else "."
        if d == root or root not in d.parents:
            break
        d = d.parent
    return None


def lock_fingerprint(manager: str, text: str) -> str:
    """npm: hash only the third-party package entries of the lockfile, so a
    workspace version bump reuses the same install. Others: the whole file."""
    if manager == "npm":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        packages = data.get("packages") if isinstance(data, dict) else None
        if isinstance(packages, dict):
            third_party = {
                k: v for k, v in packages.items()
                if "node_modules/" in k and not (isinstance(v, dict) and v.get("link"))
            }
            return hashlib.sha256(json.dumps(third_party, sort_keys=True).encode()).hexdigest()
    return hashlib.sha256(text.encode()).hexdigest()


def _install_cmd(manager: str, root: Path) -> list[str]:
    if manager == "npm":
        return ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"]
    if manager == "pnpm":
        return ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"]
    if (root / ".yarnrc.yml").is_file():  # yarn berry
        return ["yarn", "install", "--immutable", "--mode=skip-build"]
    return ["yarn", "install", "--frozen-lockfile", "--ignore-scripts"]


_LIFECYCLE = ("preinstall", "install", "postinstall", "prepublish", "preprepare", "prepare",
              "postprepare", "dependencies")


def _strip_lifecycle(path: Path) -> None:
    """Belt and braces next to --ignore-scripts (some npm versions still run a
    workspace's ``prepare``): drop lifecycle scripts from the install copy."""
    pkg = _read_json(path)
    scripts = pkg.get("scripts")
    if isinstance(scripts, dict) and any(k in scripts for k in _LIFECYCLE):
        pkg["scripts"] = {k: v for k, v in scripts.items() if k not in _LIFECYCLE}
        path.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")


def _js_root() -> Path:
    return Path(settings.workspace_dir).resolve() / "js-deps"


def _manifests(worktree: Path) -> list[str]:
    listing = git_service._run(worktree, "ls-files")
    return sorted(
        p for p in listing.splitlines()
        if Path(p).name in MANIFEST_NAMES and "node_modules/" not in p
    )


@dataclass
class Prepared:
    checkout: Path
    key: str
    manager: str
    seconds: float
    cached: bool


def _run_install(cmd: list[str], checkout: Path, env: dict[str, str], timeout: float) -> tuple[int | None, str]:
    proc = subprocess.Popen(cmd, cwd=checkout, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, verifier._kill_group(proc).decode("utf-8", "replace")
    return proc.returncode, (out or b"").decode("utf-8", "replace")


def prepare_deps(repo: Path, worktree: Path, node: str) -> Prepared:
    """The shared dependency install for this worktree's lockfile (cached)."""
    if not verifier.repo_allowed(repo):
        raise JsSetupError("repo is not under the allowed roots")
    found = package_manager(worktree)
    if found is None:
        raise JsSetupError("no lockfile at the repo root (package-lock.json, pnpm-lock.yaml or yarn.lock)")
    manager, lock = found
    key = f"{manager}-{lock_fingerprint(manager, (worktree / lock).read_text(encoding='utf-8'))[:16]}"
    repo_dir = _js_root() / hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:12]
    target = repo_dir / key
    ready = target / ".mg-ready.json"

    def cached() -> Prepared | None:
        if ready.is_file():
            meta = _read_json(ready)
            return Prepared(target / "checkout", key, manager, float(meta.get("seconds", 0.0)), True)
        return None

    if (hit := cached()) is not None:
        return hit
    repo_dir.mkdir(parents=True, exist_ok=True)
    with open(repo_dir / f"{key}.lock", "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        if (hit := cached()) is not None:
            return hit
        shutil.rmtree(target, ignore_errors=True)
        checkout = target / "checkout"
        checkout.mkdir(parents=True)
        for rel in _manifests(worktree):
            dest = checkout / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(worktree / rel, dest)
            if dest.name == "package.json":
                _strip_lifecycle(dest)
        cmd = _install_cmd(manager, checkout)
        tool = find_tool(cmd[0], node)
        if tool is None:
            raise JsSetupError(f"{cmd[0]} not found (next to {node}, Homebrew, /usr/local/bin)")
        cmd[0] = tool
        env = tool_env(target / "home", node, tool)
        env["npm_config_cache"] = str(repo_dir / "npm-cache")
        started = time.monotonic()
        code, output = _run_install(cmd, checkout, env, float(settings.branch_tests_js_install_seconds))
        seconds = round(time.monotonic() - started, 1)
        if code != 0:
            tail = " | ".join(output.strip().splitlines()[-3:])[:300]
            what = "timed out" if code is None else f"exited {code}"
            raise JsSetupError(f"{' '.join(cmd[:2])} {what} after {seconds:.0f}s: {tail}")
        ready.write_text(json.dumps({"seconds": seconds, "manager": manager, "lock": lock}))
        return Prepared(checkout, key, manager, seconds, False)


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _link_entry(src: Path, dest: Path, prep: str, worktree: Path, workspace: dict[str, str]) -> None:
    if src.is_symlink():
        real = os.path.realpath(src)
        if _inside(real, prep) and "node_modules" not in Path(os.path.relpath(real, prep)).parts:
            rel = os.path.relpath(real, prep)  # a workspace package: the worktree's own copy
            name = dest.name if not dest.parent.name.startswith("@") else f"{dest.parent.name}/{dest.name}"
            workspace.setdefault(name, rel)
            target = str(worktree / rel)
        else:
            target = real
    else:
        target = str(src)
    if not os.path.lexists(dest):
        os.symlink(target, dest)


def _mirror(src_nm: Path, dest_nm: Path, prep: str, worktree: Path, workspace: dict[str, str]) -> None:
    if dest_nm.is_symlink() or (dest_nm.exists() and not dest_nm.is_dir()):
        return
    dest_nm.mkdir(parents=True, exist_ok=True)
    for entry in sorted(os.scandir(src_nm), key=lambda e: e.name):
        if entry.name in _SKIP_ENTRIES:
            continue
        src = Path(entry.path)
        if entry.name.startswith("@") and entry.is_dir(follow_symlinks=False):
            scope = dest_nm / entry.name
            scope.mkdir(exist_ok=True)
            for sub in sorted(os.scandir(src), key=lambda e: e.name):
                _link_entry(Path(sub.path), scope / sub.name, prep, worktree, workspace)
        else:
            _link_entry(src, dest_nm / entry.name, prep, worktree, workspace)


def link_deps(prepared: Prepared, worktree: Path) -> dict[str, str]:
    """Give ``worktree`` node_modules that point into the prepared install.
    Returns the workspace packages found (name -> dir relative to the root)."""
    prep = os.path.realpath(prepared.checkout)
    workspace: dict[str, str] = {}
    dirs = {Path(rel).parent for rel in _manifests(worktree) if Path(rel).name == "package.json"}
    for rel_dir in sorted(dirs, key=lambda d: len(d.parts)):
        src_nm = Path(prep) / rel_dir / "node_modules"
        if src_nm.is_dir():
            _mirror(src_nm, worktree / rel_dir / "node_modules", prep, worktree, workspace)
    return workspace


def _entry(pkg: dict) -> str | None:
    """The package's main entry file (``exports["."]`` or ``main``)."""
    exports = pkg.get("exports")
    node = exports.get(".", exports) if isinstance(exports, dict) else exports
    for _ in range(4):
        if isinstance(node, dict):
            node = next((node[k] for k in ("import", "node", "default", "require") if k in node), None)
    if isinstance(node, str):
        return node
    main = pkg.get("main")
    return main if isinstance(main, str) else None


def missing_output(pkg_dir: Path, pkg: dict) -> str | None:
    """The build output dir (e.g. ``dist``) when the entry is a missing build
    output; None when it exists or points at source."""
    entry = _entry(pkg)
    if not entry:
        return None
    rel = Path(entry.removeprefix("./"))
    if rel.suffix in (".ts", ".tsx", ".mts", ".cts") or (pkg_dir / rel).exists():
        return None
    return rel.parts[0] if len(rel.parts) > 1 else None


def clone_tree(src: Path, dest: Path) -> None:
    """Copy a directory, copy-on-write where the filesystem allows."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    cmd = ["cp", "-cR", str(src), str(dest)] if sys.platform == "darwin" else \
        ["cp", "-a", "--reflink=auto", str(src), str(dest)]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest, symlinks=True)


def _build_cmd(manager: str) -> list[str]:
    return {"pnpm": ["pnpm", "run", "build"], "yarn": ["yarn", "run", "build"]}.get(
        manager, ["npm", "run", "build", "--silent"])


@dataclass
class BuildLog:
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0


def ensure_builds(
    worktree: Path, tmp_root: Path, pkg_rel: str, workspace: dict[str, str], manager: str,
    node: str, cache_dir: Path, log: BuildLog, deadline: float,
) -> None:
    """Build (or restore from cache) the workspace packages the test package
    imports through a missing build output, dependencies first."""
    tree = git_service._run(worktree, "write-tree").strip()
    by_dir = {d: n for n, d in workspace.items()}
    order: list[str] = []
    seen: set[str] = set()

    def visit(rel: str) -> None:
        if rel in seen:
            return
        seen.add(rel)
        pkg = _read_json(worktree / rel / "package.json")
        for dep in sorted(_dep_names(pkg)):
            if dep in workspace:
                visit(workspace[dep])
        order.append(rel)

    visit(pkg_rel)
    for rel in order:
        if rel == pkg_rel:
            continue
        pkg_dir = worktree / rel
        pkg = _read_json(pkg_dir / "package.json")
        out = missing_output(pkg_dir, pkg)
        if out is None:
            continue
        scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
        if "build" not in scripts:
            log.notes.append(f"{rel}: build output {out}/ missing and no build script")
            continue
        try:
            sub = git_service._run(worktree, "rev-parse", f"{tree}:{rel}").strip()
        except git_service.GitError:
            sub = tree
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", by_dir.get(rel, rel))
        cached = cache_dir / f"{name}-{sub[:16]}" / out
        if cached.is_dir():
            clone_tree(cached, pkg_dir / out)
            log.notes.append(f"{rel}: build restored from cache")
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.notes.append(f"{rel}: not built, build time budget used up")
            continue
        build = _build_cmd(manager)
        tool = find_tool(build[0], node)
        if tool is None:
            log.notes.append(f"{rel}: not built, {build[0]} not found")
            continue
        env = tool_env(tmp_root / "home", node, tool)
        cmd = [*verifier._sandbox_prefix(tmp_root), tool, *build[1:]]
        started = time.monotonic()
        code, output = _run_install(cmd, pkg_dir, env, remaining)
        took = round(time.monotonic() - started, 1)
        log.seconds += took
        if code == 0 and (pkg_dir / out).is_dir():
            staging = cache_dir / f".tmp-{name}-{sub[:16]}-{os.getpid()}"
            clone_tree(pkg_dir / out, staging / out)
            final = cache_dir / f"{name}-{sub[:16]}"
            if not final.exists():
                staging.rename(final)
            else:
                shutil.rmtree(staging, ignore_errors=True)
            log.notes.append(f"{rel}: built in {took:.1f}s")
        else:
            what = "timed out" if code is None else f"failed (exit {code})"
            tail = " | ".join(output.strip().splitlines()[-2:])[:200]
            log.notes.append(f"{rel}: build {what} after {took:.1f}s: {tail}")


def runner_bin(pkg_dir: Path, root: Path, runner: str) -> str | None:
    d = pkg_dir
    while True:
        pj = d / "node_modules" / runner / "package.json"
        if pj.is_file():
            pkg = _read_json(pj)
            bins = pkg.get("bin")
            rel = bins.get(runner) if isinstance(bins, dict) else bins
            if isinstance(rel, str):
                return os.path.realpath(pj.parent / rel)
        if d == root or root not in d.parents:
            return None
        d = d.parent


def _excerpt(text: str) -> str:
    lines = [ln.strip() for ln in _ANSI.sub("", text or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith("at ")][:3]
    picked = " | ".join(lines)
    return picked if len(picked) <= EXCERPT_CHARS else picked[:EXCERPT_CHARS].rstrip() + "..."


def json_results(report: Path, root: Path) -> dict[str, tuple[str, str]]:
    """Test id (``path::describe > test``) -> (outcome, excerpt) from a
    vitest/jest ``--json`` report. A file that fails to load is reported
    under its path with outcome ``error``."""
    data = _read_json(report)
    out: dict[str, tuple[str, str]] = {}
    real_root = os.path.realpath(root)
    for result in data.get("testResults") or []:
        if not isinstance(result, dict):
            continue
        path = os.path.relpath(os.path.realpath(str(result.get("name", ""))), real_root)
        asserts = result.get("assertionResults") or []
        if not asserts:
            if result.get("status") == "failed":
                out[path] = ("error", _excerpt(str(result.get("message", ""))))
            continue
        for item in asserts:
            title = " > ".join([*item.get("ancestorTitles", []), item.get("title", "")]).strip()
            test_id = f"{path}::{title}"
            n = 2
            while test_id in out:
                test_id, n = f"{path}::{title} #{n}", n + 1
            status = item.get("status")
            outcome = status if status in ("passed", "failed") else "skipped"
            excerpt = _excerpt("\n".join(item.get("failureMessages") or [])) if outcome == "failed" else ""
            out[test_id] = (outcome, excerpt)
    return out


def run_tests(
    worktree: Path, tmp_root: Path, *, runner: str, pkg_rel: str, files: list[str], node: str,
    timeout: float, tag: str,
) -> tuple[dict[str, tuple[str, str]], int | None, bool, float, str]:
    """(results, exit code, timed out, seconds, problem) for one package's test files."""
    root = worktree.resolve()
    pkg_dir = root / pkg_rel
    binary = runner_bin(pkg_dir, root, runner)
    if binary is None:
        return {}, None, False, 0.0, f"{runner} is not installed for {pkg_rel}"
    report = tmp_root / f"js-{tag}.json"
    report.unlink(missing_ok=True)
    guard = tmp_root / "mg_net_guard.cjs"
    if not guard.exists():
        guard.write_text(NET_GUARD, encoding="utf-8")
    rel_files = [os.path.relpath(root / f, pkg_dir) for f in files]
    if runner == "vitest":
        args = ["run", "--reporter=json", f"--outputFile={report}", "--coverage.enabled=false",
                "--passWithNoTests", *rel_files]
    else:
        args = ["--ci", "--json", f"--outputFile={report}", "--watchman=false",
                "--runTestsByPath", *rel_files]
    env = tool_env(tmp_root / "home", node)
    env["NODE_OPTIONS"] = f"--require={guard}"
    # Loopback only: test servers on localhost (supertest, a peer app on port 0).
    cmd = [*verifier._sandbox_prefix(tmp_root, loopback=True), node, binary, *args]
    started = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=pkg_dir, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        out = verifier._kill_group(proc)
    seconds = round(time.monotonic() - started, 2)
    results = json_results(report, root)
    problem = ""
    if not results and not timed_out and proc.returncode not in (0, None):
        problem = _excerpt((out or b"").decode("utf-8", "replace")[-2000:])
    return results, (None if timed_out else proc.returncode), timed_out, seconds, problem
