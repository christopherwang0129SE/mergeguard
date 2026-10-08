"""JS/TS branch tests: real git worktrees, real node, a tiny stand-in for
vitest (no network, no npm install: the dependency install is replaced by a
prepared ``node_modules``)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.services import branch_tests, js_tests

NODE = js_tests.node_binary()
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Minimal vitest stand-in: CJS test files call the global test(name, fn); the
# report has the shape of `vitest run --reporter=json`.
FAKE_VITEST = r"""#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const args = process.argv.slice(2).filter((a) => a !== 'run');
const out = (args.find((a) => a.startsWith('--outputFile=')) || '').slice(13);
const files = args.filter((a) => !a.startsWith('--'));
const report = { testResults: [] };
let failed = false;
for (const file of files) {
  const abs = path.resolve(file);
  const tests = [];
  global.test = (name, fn) => tests.push([name, fn]);
  global.assertEqual = (got, want) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) {
      throw new Error('AssertionError: expected ' + JSON.stringify(got) + ' to equal ' + JSON.stringify(want));
    }
  };
  try {
    require(abs);
  } catch (err) {
    failed = true;
    report.testResults.push({ name: abs, status: 'failed', message: String(err.message), assertionResults: [] });
    continue;
  }
  const results = [];
  for (const [name, fn] of tests) {
    try { fn(); results.push({ ancestorTitles: ['suite'], title: name, status: 'passed', failureMessages: [] }); }
    catch (err) { failed = true; results.push({ ancestorTitles: ['suite'], title: name, status: 'failed', failureMessages: [String(err.message)] }); }
  }
  report.testResults.push({ name: abs, status: 'passed', assertionResults: results });
}
fs.writeFileSync(out, JSON.stringify(report));
process.exit(failed ? 1 : 0);
"""

STORE = """function readFloor() {
  return { proven: 3 };
}

module.exports = { readFloor, describe: () => 'store' };
""" + "".join(f"// filler {i}\n" for i in range(12))
STORE_TUPLE = STORE.replace("return { proven: 3 };", "return [{ proven: 3 }, []];")
TEST_STORE = """const { readFloor } = require('./store.js');
test('reads the floor', () => { assertEqual(readFloor().proven, 3); });
"""
TEST_STORE_TUPLE = """const { readFloor } = require('./store.js');
test('reads the floor', () => { const [floor, problems] = readFloor(); assertEqual([floor.proven, problems], [3, []]); });
"""
REPORT = """const { readFloor } = require('./store.js');
module.exports = { proven: () => readFloor().proven || 0 };
"""
TEST_REPORT = """const { proven } = require('./report.js');
test('proven count', () => { assertEqual(proven(), 3); });
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


def _branch(repo: Path, name: str, files: dict[str, str], msg: str) -> None:
    _git(repo, "checkout", "-q", "-b", name, "main")
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    _git(repo, "checkout", "-q", "main")


@pytest.fixture()
def jsrepo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "allowed" / "floor-js"
    r.mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "package.json").write_text(json.dumps(
        {"name": "floor-js", "private": True, "devDependencies": {"vitest": "^3.0.0"}}))
    (r / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {
        "": {"name": "floor-js"}, "node_modules/vitest": {"version": "3.0.0"}}}))
    (r / "src").mkdir()
    (r / "src" / "store.js").write_text(STORE)
    (r / "src" / "store.test.js").write_text(TEST_STORE)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    _branch(r, "feat/tuple", {"src/store.js": STORE_TUPLE, "src/store.test.js": TEST_STORE_TUPLE},
            "readFloor returns [floor, problems]")
    _branch(r, "feat/report", {"src/report.js": REPORT, "src/report.test.js": TEST_REPORT},
            "report proven count")
    # Harmless TS-side pair: a new helper and its test, nothing shared.
    _branch(r, "feat/describe", {
        "src/describe.js": "module.exports = { describeStore: () => 'store' };\n",
        "src/describe.test.js": "const { describeStore } = require('./describe.js');\n"
                                "test('describes', () => { assertEqual(describeStore(), 'store'); });\n",
    }, "describe")

    # The shared install, prepared without network: node_modules/vitest is the stand-in.
    install = tmp_path / "deps" / "checkout"
    bin_dir = install / "node_modules" / "vitest"
    bin_dir.mkdir(parents=True)
    (bin_dir / "package.json").write_text(json.dumps({"name": "vitest", "bin": {"vitest": "vitest.cjs"}}))
    (bin_dir / "vitest.cjs").write_text(FAKE_VITEST)
    calls: list[Path] = []

    def fake_prepare(repo: Path, worktree: Path, node: str) -> js_tests.Prepared:
        assert js_tests.package_manager(worktree) == ("npm", "package-lock.json")
        calls.append(worktree)
        return js_tests.Prepared(install, "npm-test", "npm", 1.5, cached=len(calls) > 1)

    monkeypatch.setattr(js_tests, "prepare_deps", fake_prepare)
    monkeypatch.setattr(settings, "verify_allowed_roots", str(tmp_path / "allowed"))
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path / "ws"))
    return r


def test_js_new_caller_against_changed_return_value_is_confirmed(jsrepo: Path) -> None:
    res = branch_tests.check_pair(jsrepo, "main", "feat/tuple", "feat/report")
    assert res.status == "confirmed", res.reason
    assert res.files_a == ["src/store.test.js"] and res.files_b == ["src/report.test.js"]
    [hit] = res.confirming
    assert hit.test_id == "src/report.test.js::suite > proven count"
    assert hit.branch == "feat/report" and hit.file == "src/report.test.js"
    assert "expected 0 to equal 3" in hit.excerpt
    # The failing test file does not exist on the merge-base: no base run for it.
    assert [r.target for r in res.runs] == ["a", "b", "merge", "merge-rerun"]
    assert {r.runner for r in res.runs} == {"vitest"} and res.runs[0].package == "."
    assert res.install_s == 1.5 and any("npm install" in s for s in res.setup)
    assert len(_git(jsrepo, "worktree", "list").splitlines()) == 1  # worktrees removed


def test_harmless_js_pair_is_not_confirmed(jsrepo: Path) -> None:
    res = branch_tests.check_pair(jsrepo, "main", "feat/tuple", "feat/describe")
    assert res.status == "passed", res.reason
    assert res.confirming == []
    merge = next(r for r in res.runs if r.target == "merge")
    assert merge.passed == 2 and merge.failed == 0


def test_js_disabled_reports_unsupported(jsrepo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "branch_tests_js_enabled", False)
    res = branch_tests.check_pair(jsrepo, "main", "feat/tuple", "feat/report")
    assert res.status == "unsupported" and "disabled" in res.reason
    assert res.unsupported_files == ["src/report.test.js", "src/store.test.js"]


def test_runner_and_package_manager_detection(tmp_path: Path) -> None:
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (tmp_path / "package.json").write_text(json.dumps({"name": "root"}))
    pkg = tmp_path / "packages" / "core"
    (pkg / "src").mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "core", "scripts": {"test": "jest"}}))
    web = tmp_path / "packages" / "web"
    (web / "src").mkdir(parents=True)
    (web / "package.json").write_text(json.dumps({"name": "web"}))
    (web / "vitest.config.ts").write_text("export default {}\n")
    other = tmp_path / "packages" / "other"
    other.mkdir()
    (other / "package.json").write_text(json.dumps({"name": "other"}))
    assert js_tests.package_manager(tmp_path) == ("pnpm", "pnpm-lock.yaml")
    assert js_tests.runner_for(tmp_path, "packages/core/src/a.test.ts") == ("jest", "packages/core")
    assert js_tests.runner_for(tmp_path, "packages/web/src/a.test.tsx") == ("vitest", "packages/web")
    assert js_tests.runner_for(tmp_path, "packages/other/a.spec.js") is None


def test_npm_lock_fingerprint_ignores_workspace_version_bumps() -> None:
    lock = {"lockfileVersion": 3, "packages": {
        "": {"name": "root", "version": "1.0.0"},
        "packages/core": {"name": "core", "version": "1.0.0"},
        "node_modules/core": {"resolved": "packages/core", "link": True},
        "node_modules/left-pad": {"version": "1.3.0", "integrity": "sha512-x"},
    }}
    bumped = json.loads(json.dumps(lock))
    bumped["packages"][""]["version"] = "1.1.0"
    bumped["packages"]["packages/core"]["version"] = "1.1.0"
    dep = json.loads(json.dumps(lock))
    dep["packages"]["node_modules/left-pad"]["version"] = "1.3.1"
    fp = js_tests.lock_fingerprint
    assert fp("npm", json.dumps(lock)) == fp("npm", json.dumps(bumped))
    assert fp("npm", json.dumps(lock)) != fp("npm", json.dumps(dep))


def test_lifecycle_scripts_are_stripped_from_the_install_copy(tmp_path: Path) -> None:
    pj = tmp_path / "package.json"
    pj.write_text(json.dumps({"name": "x", "scripts": {"prepare": "husky", "postinstall": "node x",
                                                       "build": "tsc", "test": "vitest"}}))
    js_tests._strip_lifecycle(pj)
    assert json.loads(pj.read_text())["scripts"] == {"build": "tsc", "test": "vitest"}


def test_json_report_ids_and_load_errors(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"testResults": [
        {"name": str(tmp_path / "src" / "a.test.ts"), "status": "failed", "assertionResults": [
            {"ancestorTitles": ["A", "b"], "title": "works", "status": "failed",
             "failureMessages": ["\x1b[31mAssertionError: expected 1 to be 2\x1b[0m\n    at x (a.ts:1)"]},
            {"ancestorTitles": [], "title": "skipped one", "status": "skipped"}]},
        {"name": str(tmp_path / "src" / "b.test.ts"), "status": "failed",
         "message": "Error: Cannot find module './gone'", "assertionResults": []},
    ]}))
    res = js_tests.json_results(report, tmp_path)
    assert res == {
        "src/a.test.ts::A > b > works": ("failed", "AssertionError: expected 1 to be 2"),
        "src/a.test.ts::skipped one": ("skipped", ""),
        "src/b.test.ts": ("error", "Error: Cannot find module './gone'"),
    }
