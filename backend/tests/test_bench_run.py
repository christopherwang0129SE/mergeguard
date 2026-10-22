"""Per-case bench runner: env isolation, budget, resume, timeout. No network."""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.bench.run import (
    analyze_case,
    child_env,
    is_confirmed,
    is_flagged,
    main,
    run_child,
)


def _case(case_id: str, repo: str = "acme/widget", **extra: object) -> dict:
    row = {
        "case_id": case_id,
        "repo": repo,
        "label": "broke",
        "gt_kind": "revert",
        "confidence": "revert",
        "fix_sha": "f" * 40,
        "fix_subject": "fix the merge",
        "a": {"sha": "a" * 40, "subject": "change A", "pr": 11, "parent": "p", "kind": "squash"},
        "b": {"sha": "b" * 40, "subject": "change B", "pr": 12, "parent": "p", "kind": "merge"},
    }
    row.update(extra)
    return row


def _verdict(
    cost: float = 0.4,
    *,
    level: str = "low",
    verification: str = "skipped",
    branch: str = "passed",
) -> dict:
    return {
        "level": level,
        "score": 12.0,
        "score_source": "git",
        "fallback_reason": "disabled",
        "model_score": None,
        "verification": {"status": verification, "reason": "", "attempts": 0},
        "branch_tests": {"status": branch, "reason": ""},
        "docs_sources": 0,
        "rationale": "independent",
        "usage": {"llm_calls": 0, "total_tokens": 0, "estimated_cost_usd": cost},
    }


def _meta(case: dict, root: Path, *, b: str | None = "bbbb") -> dict:
    return {
        "path": str(Path(root) / str(case["case_id"])),
        "main": "m",
        "a": "aaaa",
        "b": b,
        "a_apply_failed": False,
        "b_apply_failed": b is None,
        "textual_conflict": False,
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _texts(root: Path) -> str:
    chunks: list[str] = []
    if not root.exists():
        return ""
    for path in root.rglob("*"):
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _patch_replay(monkeypatch: pytest.MonkeyPatch, captured: list | None = None, b: str | None = "bbbb") -> None:
    def fake(src: Path, case: dict, root: Path) -> dict:
        if captured is not None:
            captured.append(Path(src))
        return _meta(case, Path(root), b=b)

    monkeypatch.setattr("scripts.bench.run.build_replay", fake)


def _invoke(tmp: Path, extra: list[str] | None = None) -> list[str]:
    return [
        "--cases",
        str(tmp / "cases.jsonl"),
        "--src-root",
        str(tmp / "src"),
        "--replay-root",
        str(tmp / "replay"),
        "--results",
        str(tmp / "results" / "run"),
        *(extra or []),
    ]


def _run(
    tmp: Path,
    rows: list[dict],
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
    child,
    *,
    b: str | None = "bbbb",
    captured: list | None = None,
    replay=None,
) -> tuple[int, Path]:
    cases = tmp / "cases.jsonl"
    _write(cases, rows)
    if replay is None:
        _patch_replay(monkeypatch, captured, b=b)
    else:
        monkeypatch.setattr("scripts.bench.run.build_replay", replay)
    monkeypatch.setattr("scripts.bench.run.run_child", child)
    results = tmp / "results" / "run"
    return main(_invoke(tmp, extra)), results


def _plant(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "keep.txt").write_text("stay", encoding="utf-8")
    venv = dest / ".venv"
    venv.mkdir(exist_ok=True)
    (venv / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")


def _spy_rmtree(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, bool]]:
    removed: list[tuple[Path, bool]] = []
    real = shutil.rmtree

    def spy(path, ignore_errors=False, **kwargs):
        removed.append((Path(path), bool(ignore_errors)))
        return real(path, ignore_errors=ignore_errors, **kwargs)

    monkeypatch.setattr("scripts.bench.run.shutil.rmtree", spy)
    return removed


def _ok_child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
    return {"status": "ok", "verdict": _verdict(0.0), "seconds": 0.01}


def test_child_env_strips_label_and_disables_when_missing(tmp_path: Path) -> None:
    env = child_env(
        {"PATH": "/usr/bin", "NEBIUS_API_KEY": "from-parent", "TOKEN_FACTORY_API_KEY_BENCH": "Some label v1.abc"},
        tmp_path,
    )
    assert env["NEBIUS_API_KEY"] == "v1.abc"
    assert env["NEBIUS_ENABLED"] == "true"
    assert env["PATH"] == "/usr/bin"
    assert "from-parent" not in env.values()
    assert env["VERIFY_ALLOWED_ROOTS"] == str(tmp_path.resolve())

    disabled = child_env({"TOKEN_FACTORY_API_KEY_BENCH": "   "}, tmp_path)
    assert disabled["NEBIUS_API_KEY"] == ""
    assert disabled["NEBIUS_ENABLED"] == "false"
    absent = child_env({})
    assert absent["NEBIUS_API_KEY"] == ""
    assert absent["NEBIUS_ENABLED"] == "false"


def test_child_env_dry_run_forces_disabled(tmp_path: Path) -> None:
    env = child_env({"TOKEN_FACTORY_API_KEY_BENCH": "label v1.live"}, tmp_path, dry_run=True)
    assert env["NEBIUS_API_KEY"] == "v1.live"
    assert env["NEBIUS_ENABLED"] == "false"


def test_key_never_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = "v1.FAKEBENCHKEY_do_not_leak"
    raw = f"Some label {token}"
    monkeypatch.setenv("TOKEN_FACTORY_API_KEY_BENCH", raw)

    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        assert env["NEBIUS_API_KEY"] == token
        assert env["NEBIUS_ENABLED"] == "false"
        assert env["VERIFY_ALLOWED_ROOTS"]
        verdict = _verdict(0.0)
        verdict["rationale"] = f"saw {token}"
        return {"status": "ok", "verdict": verdict, "stderr": f"boom {raw}", "seconds": 0.01}

    rc, results = _run(tmp_path, [_case("c1")], monkeypatch, ["--dry-run", "--max-usd", "9"], child)
    assert rc == 0
    blob = _texts(tmp_path / "results")
    assert token not in blob
    assert raw not in blob
    assert "[redacted]" in blob


def test_budget_stops_after_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        calls.append(Path(replay_path).name)
        return {"status": "ok", "verdict": _verdict(0.4), "seconds": 0.01}

    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    _write(first, [_case(f"c{i}") for i in range(3)])
    _write(second, [_case(f"c{i}") for i in range(3, 5)])
    _patch_replay(monkeypatch)
    monkeypatch.setattr("scripts.bench.run.run_child", child)
    results = tmp_path / "results" / "run"
    rc = main(
        [
            "--cases",
            str(first),
            "--cases",
            str(second),
            "--src-root",
            str(tmp_path / "src"),
            "--replay-root",
            str(tmp_path / "replay"),
            "--results",
            str(results),
            "--max-usd",
            "1",
            "--dry-run",
        ]
    )
    assert rc == 0
    rows = _read(results / "results.jsonl")
    assert [row["case_id"] for row in rows] == ["c0", "c1"]
    assert calls == ["c0", "c1"]
    spend = json.loads((results.parent / "spend.json").read_text(encoding="utf-8"))
    assert spend["cases"] == 2
    assert spend["spent_usd"] == pytest.approx(0.8)
    assert rows[0]["cost"] == pytest.approx(0.4)
    assert rows[0]["a"]["pr"] == 11
    assert rows[0]["b"]["subject"] == "change B"


def test_resume_skips_done_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        seen.append(Path(replay_path).name)
        return {"status": "ok", "verdict": _verdict(0.0), "seconds": 0.02}

    results = tmp_path / "results" / "run"
    results.mkdir(parents=True)
    (results / "results.jsonl").write_text(json.dumps({"case_id": "done", "status": "ok"}) + "\n", encoding="utf-8")
    rc, results = _run(tmp_path, [_case("done"), _case("new")], monkeypatch, ["--dry-run"], child)
    assert rc == 0
    assert seen == ["new"]
    ids = [row["case_id"] for row in _read(results / "results.jsonl")]
    assert ids == ["done", "new"]


def test_timeout_kills_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent_pid = tmp_path / "parent.pid"
    child_pid = tmp_path / "child.pid"
    script = (
        "import os, sys, time, pathlib\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    pathlib.Path(sys.argv[2]).write_text(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(
        "scripts.bench.run._child_argv",
        lambda _path: [sys.executable, "-c", script, str(parent_pid), str(child_pid)],
    )
    outcome = run_child(tmp_path, cwd=tmp_path, env=os.environ.copy(), timeout=1.0)
    assert outcome["status"] == "timeout"
    assert parent_pid.is_file()
    assert child_pid.is_file()

    def alive(pid: int) -> bool:
        status = Path(f"/proc/{pid}/status")
        if not status.exists():
            return False
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("State:"):
                return not line.split()[1].startswith("Z")
        return True

    assert not alive(int(parent_pid.read_text()))
    assert not alive(int(child_pid.read_text()))

    def injected(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        return {"status": "timeout", "seconds": 0.2}

    rc, results = _run(tmp_path, [_case("slow")], monkeypatch, ["--dry-run", "--timeout", "0.01"], injected)
    assert rc == 0
    row = _read(results / "results.jsonl")[-1]
    assert row["case_id"] == "slow"
    assert row["status"] == "timeout"
    assert row["cost"] == 0


def test_replay_failed_when_b_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def child(*_args, **_kwargs):
        raise AssertionError("child should not run")

    rc, results = _run(tmp_path, [_case("missing-b")], monkeypatch, ["--dry-run"], child, b=None)
    assert rc == 0
    row = _read(results / "results.jsonl")[0]
    assert row["status"] == "replay_failed"
    assert row["b_apply_failed"] is True
    assert row["a"]["sha"] == "a" * 40
    assert "parallel_evidence" not in row


@pytest.mark.parametrize("flag", ["b_empty", "a_empty"])
def test_replay_failed_when_branch_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    analysed: list[str] = []

    def child(*_args, **_kwargs):
        analysed.append("run_child")
        raise AssertionError("analysis should not run")

    def analyze(*_args, **_kwargs):
        analysed.append("analyze_case")
        raise AssertionError("analyze_case should not run")

    def replay(src: Path, case: dict, root: Path) -> dict:
        if case["case_id"] == "missing-b":
            return _meta(case, Path(root), b=None)
        meta = _meta(case, Path(root), b="bbbb")
        meta["a_empty"] = flag == "a_empty"
        meta["b_empty"] = flag == "b_empty"
        return meta

    monkeypatch.setattr("scripts.bench.run.analyze_case", analyze)
    rc, results = _run(
        tmp_path,
        [_case("empty-b"), _case("missing-b")],
        monkeypatch,
        ["--dry-run"],
        child,
        replay=replay,
    )
    assert rc == 0
    assert analysed == []
    empty, missing = _read(results / "results.jsonl")
    assert empty["case_id"] == "empty-b" and missing["case_id"] == "missing-b"
    assert empty["status"] == "replay_failed" and missing["status"] == "replay_failed"
    assert set(empty) == set(missing)
    assert empty["b_apply_failed"] is False and missing["b_apply_failed"] is True
    assert empty["venv_ok"] is None and empty["cost"] == 0
    assert empty["a"]["sha"] == "a" * 40 and empty["b"]["sha"] == "b" * 40
    assert "level" not in empty and "flagged" not in empty and "error" not in empty


def test_replay_failed_records_parallel_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src: Path, case: dict, root: Path) -> dict:
        raise RuntimeError("clone failed")

    monkeypatch.setattr("scripts.bench.run.build_replay", boom)
    cases = tmp_path / "cases.jsonl"
    _write(cases, [_case("bad", **{"parallel_evidence": {"notes": 1}})])
    results = tmp_path / "out" / "run"
    rc = main(
        [
            "--cases",
            str(cases),
            "--src-root",
            str(tmp_path / "src"),
            "--replay-root",
            str(tmp_path / "replay"),
            "--results",
            str(results),
            "--dry-run",
        ]
    )
    assert rc == 0
    row = _read(results / "results.jsonl")[0]
    assert row["status"] == "replay_failed"
    assert row["parallel_evidence"] == {"notes": 1}
    assert "RuntimeError" in row["error"]


def test_src_repo_is_owner_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []

    def child(*_a, **_k):
        return {"status": "ok", "verdict": _verdict(0.0), "seconds": 0.0}

    rc, _results = _run(
        tmp_path,
        [_case("slash", repo="acme/widget"), _case("flat", repo="acme__widget")],
        monkeypatch,
        ["--dry-run", "--max-usd", "9"],
        child,
        captured=seen,
    )
    assert rc == 0
    assert seen == [tmp_path / "src" / "acme__widget", tmp_path / "src" / "acme__widget"]


def test_flagged_confirmed_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert is_flagged(_verdict(level="high")) is True
    assert is_confirmed(_verdict(level="high")) is False
    assert is_flagged(_verdict(verification="confirmed")) is True
    assert is_confirmed(_verdict(verification="confirmed")) is True
    assert is_flagged(_verdict(branch="confirmed")) is True
    assert is_confirmed(_verdict(branch="confirmed")) is True
    assert is_flagged(_verdict()) is False
    assert is_confirmed(_verdict()) is False

    def child(*_a, **_k):
        return {
            "status": "ok",
            "verdict": _verdict(0.0, level="medium", verification="suspected", branch="confirmed"),
            "seconds": 0.01,
        }

    _rc, results = _run(tmp_path, [_case("flag")], monkeypatch, ["--dry-run"], child)
    row = _read(results / "results.jsonl")[0]
    assert row["flagged"] is True
    assert row["confirmed"] is True
    assert row["verification"]["status"] == "suspected"
    assert row["branch_tests"]["status"] == "confirmed"


def test_analyze_case_projects_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class Pair:
        level = "high"
        score = 91.5
        score_source = "model"
        fallback_reason = None
        model_score = 88.0
        verification = type("V", (), {"status": "confirmed", "reason": "broke", "attempts": 2})()
        branch_tests = type("B", (), {"status": "passed", "reason": "ok"})()
        docs_sources = [1, 2, 3]
        rationale = "r" * 500
        finding = "semantic"
        textual_conflict = False
        git_score = 40.0
        model_level = "high"
        shared_files = 2
        overlapping_files = 1
        symbol_overlap = ["a", "b"]
        static_breaks = ["sig"]
        prefilter_candidate = True

    class Usage:
        llm_calls = 2
        total_tokens = 40
        estimated_cost_usd = 0.02

    def fake_analyze(repo, base, branches, **kwargs):
        captured.update(repo=repo, base=base, branches=branches, **kwargs)
        return type("R", (), {"pairs": [Pair()], "usage": Usage()})()

    import app.services.sentinel as sentinel_mod
    import app.services.verifier as verifier_mod

    monkeypatch.setattr(sentinel_mod, "analyze", fake_analyze)
    monkeypatch.setattr(verifier_mod, "repo_allowed", lambda repo: True)
    monkeypatch.setenv("MG_BENCH_VERIFY", "0")
    payload = analyze_case(tmp_path)
    assert captured["base"] == "main"
    assert captured["branches"] == ["a", "b"]
    assert captured["use_cache"] is True
    assert captured["verify"] is False
    assert captured["verify_allowed"] is True
    assert payload["level"] == "high"
    assert payload["docs_sources"] == 3
    assert payload["rationale"] == "r" * 300
    assert payload["verification"] == {"status": "confirmed", "reason": "broke", "attempts": 2}
    assert payload["branch_tests"] == {"status": "passed", "reason": "ok"}
    assert payload["usage"]["llm_calls"] == 2
    assert payload["finding"] == "semantic"
    assert payload["app_textual_conflict"] is False
    assert payload["git_score"] == 40.0
    assert payload["model_level"] == "high"
    assert payload["shared_files"] == 2
    assert payload["overlapping_files"] == 1
    assert payload["symbol_overlap"] == 2
    assert payload["static_breaks"] == 1
    assert payload["prefilter_candidate"] is True
    assert is_flagged(payload) is True
    assert is_confirmed(payload) is True


def test_textual_conflict_finding_is_not_flagged() -> None:
    plain = _verdict(level="high")
    assert "finding" not in plain
    assert is_flagged(plain) is True

    conflict = _verdict(level="high", verification="confirmed", branch="confirmed")
    conflict["finding"] = "textual_conflict"
    assert is_flagged(conflict) is False
    assert is_confirmed(conflict) is True

    semantic = _verdict(level="high")
    semantic["finding"] = "semantic"
    assert is_flagged(semantic) is True


def test_child_crash_scrubs_stderr(tmp_path: Path) -> None:
    token = "v1.CRASHKEY_should_not_stick"
    outcome = run_child(
        tmp_path,
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "TOKEN_FACTORY_API_KEY_BENCH": f"label {token}"},
        timeout=30,
        argv=[sys.executable, "-c", f"import sys; sys.stderr.write('nope {token}\\n'); raise SystemExit(2)"],
    )
    assert outcome["status"] == "error"
    assert token not in outcome["stderr"]
    assert "nope" in outcome["stderr"]


def test_drops_replay_venv_after_each_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = tmp_path / "replay"
    (replay / ".venv").mkdir(parents=True)
    (replay / ".venv" / "root").write_text("keep", encoding="utf-8")
    (replay / "other" / ".venv").mkdir(parents=True)
    (replay / "other" / ".venv" / "secret").write_text("keep", encoding="utf-8")
    removed = _spy_rmtree(monkeypatch)

    def fake(src: Path, case: dict, root: Path) -> dict:
        dest = Path(root) / str(case["case_id"])
        _plant(dest)
        if case["case_id"] == "exploded":
            raise RuntimeError("clone failed")
        return _meta(case, Path(root), b=None if case["case_id"] == "missing" else "bbbb")

    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        name = Path(replay_path).name
        if name == "timeout":
            assert not (replay / "ok" / ".venv").exists()
            assert not (replay / "ok" / ".mg-workspace").exists()
            return {"status": "timeout", "seconds": 0.2}
        if name == "error":
            raise RuntimeError("child blew up")
        return {"status": "ok", "verdict": _verdict(0.0), "seconds": 0.01}

    rows = [_case("ok"), _case("timeout"), _case("error"), _case("missing"), _case("exploded")]
    rc, results = _run(tmp_path, rows, monkeypatch, ["--dry-run"], child, replay=fake)
    assert rc == 0
    got = [(row["case_id"], row["status"]) for row in _read(results / "results.jsonl")]
    assert got == [
        ("ok", "ok"),
        ("timeout", "timeout"),
        ("error", "error"),
        ("missing", "replay_failed"),
        ("exploded", "replay_failed"),
    ]
    assert "child blew up" in _read(results / "results.jsonl")[2]["error"]
    assert "RuntimeError" in _read(results / "results.jsonl")[4]["error"]
    for case_id in ("ok", "timeout", "error", "missing", "exploded"):
        assert (replay / case_id / "keep.txt").read_text(encoding="utf-8") == "stay"
        assert not (replay / case_id / ".venv").exists()
        assert not (replay / case_id / ".mg-workspace").exists()
    assert (replay / ".venv" / "root").read_text(encoding="utf-8") == "keep"
    assert (replay / "other" / ".venv" / "secret").read_text(encoding="utf-8") == "keep"
    root = replay.resolve()
    assert [(path.parent.name, path.name) for path, _flag in removed] == [
        ("ok", ".venv"),
        ("ok", ".mg-workspace"),
        ("timeout", ".venv"),
        ("timeout", ".mg-workspace"),
        ("error", ".venv"),
        ("error", ".mg-workspace"),
        ("missing", ".venv"),
        ("missing", ".mg-workspace"),
        ("exploded", ".venv"),
        ("exploded", ".mg-workspace"),
    ]
    for path, ignore_errors in removed:
        assert ignore_errors is True
        assert path.name in {".venv", ".mg-workspace"}
        assert path.resolve().parent.parent == root


def test_keep_venv_leaves_dot_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    removed = _spy_rmtree(monkeypatch)

    def fake(src: Path, case: dict, root: Path) -> dict:
        dest = Path(root) / str(case["case_id"])
        _plant(dest)
        if case["case_id"] == "missing":
            return _meta(case, Path(root), b=None)
        return _meta(case, Path(root))

    rc, _results = _run(
        tmp_path,
        [_case("ok"), _case("missing")],
        monkeypatch,
        ["--dry-run", "--keep-venv"],
        _ok_child,
        replay=fake,
    )
    assert rc == 0
    assert removed == []
    for case_id in ("ok", "missing"):
        venv = tmp_path / "replay" / case_id / ".venv" / "pyvenv.cfg"
        assert venv.read_text(encoding="utf-8").startswith("home")
    assert (tmp_path / "replay" / "ok" / ".mg-workspace").is_dir()
    assert not (tmp_path / "replay" / "missing" / ".mg-workspace").exists()


def test_venv_delete_stays_inside_replay_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = tmp_path / "replay"
    replay.mkdir()
    (replay / ".venv").mkdir()
    (replay / ".venv" / "secret").write_text("root", encoding="utf-8")
    outside = tmp_path / "outside"
    _plant(outside)
    escaped = tmp_path / "escaped"
    _plant(escaped)
    linked = tmp_path / "linked"
    _plant(linked)
    (replay / "linked").symlink_to(linked, target_is_directory=True)
    removed = _spy_rmtree(monkeypatch)

    def fake(src: Path, case: dict, root: Path) -> dict:
        cid = str(case["case_id"])
        meta = _meta(case, Path(root))
        if cid == "outside":
            meta["path"] = str(outside)
        elif cid == "dotdot":
            meta["path"] = str(Path(root) / ".." / "escaped")
        elif cid == "rootpath":
            meta["path"] = str(Path(root))
        elif cid == "safe":
            _plant(Path(root) / cid)
        return meta

    rc, results = _run(
        tmp_path,
        [_case("safe"), _case("outside"), _case("dotdot"), _case("linked"), _case("rootpath")],
        monkeypatch,
        ["--dry-run"],
        _ok_child,
        replay=fake,
    )
    assert rc == 0
    assert [row["case_id"] for row in _read(results / "results.jsonl")] == [
        "safe", "outside", "dotdot", "linked", "rootpath",
    ]
    assert not (replay / "safe" / ".venv").exists()
    assert (replay / "safe" / "keep.txt").read_text(encoding="utf-8") == "stay"
    assert (outside / ".venv" / "pyvenv.cfg").is_file()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "stay"
    assert (escaped / ".venv" / "pyvenv.cfg").is_file()
    assert (escaped / "keep.txt").read_text(encoding="utf-8") == "stay"
    assert (linked / ".venv" / "pyvenv.cfg").is_file()
    assert (linked / "keep.txt").read_text(encoding="utf-8") == "stay"
    assert (replay / "linked").is_symlink()
    assert (replay / ".venv" / "secret").read_text(encoding="utf-8") == "root"
    assert not (replay / "safe" / ".mg-workspace").exists()
    assert not (outside / ".mg-workspace").exists()
    assert not (escaped / ".mg-workspace").exists()
    assert not (linked / ".mg-workspace").exists()
    assert not (replay / ".mg-workspace").exists()
    assert [path.name for path, _flag in removed] == [".venv", ".mg-workspace"]
    assert [path.resolve().parent.name for path, _flag in removed] == ["safe", "safe"]
    assert all(flag for _path, flag in removed)


def test_low_disk_writes_nothing_and_resume_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    gib = 1024 ** 3
    state = {"free": 6 * gib}
    monkeypatch.setattr(
        "scripts.bench.run.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=state["free"] + 1, used=1, free=state["free"]),
    )
    built: list[str] = []

    def fake(src: Path, case: dict, root: Path) -> dict:
        built.append(str(case["case_id"]))
        return _meta(case, Path(root))

    ran: list[str] = []

    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        ran.append(Path(replay_path).name)
        return {"status": "ok", "verdict": _verdict(0.0), "seconds": 0.01}

    extra = ["--dry-run", "--min-free-gb", "10"]
    rc, results = _run(tmp_path, [_case("c1"), _case("c2")], monkeypatch, extra, child, replay=fake)
    assert rc == 3
    assert built == []
    assert ran == []
    assert not (results / "results.jsonl").exists()
    assert not (results.parent / "spend.json").exists()
    err = capsys.readouterr().err
    assert "disk stop free=6.00GB below min=10GB case=c1; result not recorded" in err

    state["free"] = 6 * gib
    rc2 = main(_invoke(tmp_path, ["--dry-run"]))
    assert rc2 == 0
    assert built == ["c1", "c2"]
    assert ran == ["c1", "c2"]
    assert [row["case_id"] for row in _read(results / "results.jsonl")] == ["c1", "c2"]


def test_disk_boundary_stops_before_next_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    gib = 1024 ** 3
    calls = {"n": 0}

    def usage(_path: Path):
        calls["n"] += 1
        free = 5 * gib if calls["n"] == 1 else 5 * gib - 1
        return SimpleNamespace(total=free + 1, used=1, free=free)

    monkeypatch.setattr("scripts.bench.run.shutil.disk_usage", usage)
    seen: list[str] = []

    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        seen.append(Path(replay_path).name)
        return {"status": "ok", "verdict": _verdict(0.0), "seconds": 0.01}

    rc, results = _run(tmp_path, [_case("c1"), _case("c2")], monkeypatch, ["--dry-run"], child)
    assert rc == 3
    assert calls["n"] == 2
    assert seen == ["c1"]
    rows = _read(results / "results.jsonl")
    assert [row["case_id"] for row in rows] == ["c1"]
    spend = json.loads((results.parent / "spend.json").read_text(encoding="utf-8"))
    assert spend["cases"] == 1
    err = capsys.readouterr().err
    assert "disk stop" in err
    assert "case=c2; result not recorded" in err
    assert "min=5" in err


@pytest.mark.parametrize("kind", ["errno", "message"])
def test_replay_enospc_stops_without_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], kind: str,
) -> None:
    phase = {"block": True}
    seen: list[str] = []

    def fake(src: Path, case: dict, root: Path) -> dict:
        seen.append(str(case["case_id"]))
        dest = Path(root) / str(case["case_id"])
        _plant(dest)
        if phase["block"] and case["case_id"] == "full":
            if kind == "errno":
                raise OSError(errno.ENOSPC, "disk full")
            raise OSError(errno.EIO, "No space left on device")
        return _meta(case, Path(root))

    ran: list[str] = []

    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        ran.append(Path(replay_path).name)
        return {"status": "ok", "verdict": _verdict(0.0), "seconds": 0.01}

    rc, results = _run(
        tmp_path, [_case("full"), _case("later")], monkeypatch, ["--dry-run"], child, replay=fake,
    )
    assert rc == 3
    assert seen == ["full"]
    assert ran == []
    assert not (results / "results.jsonl").exists()
    assert not (results.parent / "spend.json").exists()
    assert not (tmp_path / "replay" / "full" / ".venv").exists()
    assert (tmp_path / "replay" / "full" / "keep.txt").read_text(encoding="utf-8") == "stay"
    err = capsys.readouterr().err
    assert "disk stop no space left on device case=full; result not recorded" in err

    phase["block"] = False
    seen.clear()
    rc2 = main(_invoke(tmp_path, ["--dry-run"]))
    assert rc2 == 0
    assert seen == ["full", "later"]
    assert ran == ["full", "later"]
    assert [row["case_id"] for row in _read(results / "results.jsonl")] == ["full", "later"]
    assert not (tmp_path / "replay" / "full" / ".venv").exists()
    assert not (tmp_path / "replay" / "later" / ".venv").exists()


def test_ordinary_oserror_is_replay_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(src: Path, case: dict, root: Path) -> dict:
        if case["case_id"] == "bad":
            dest = Path(root) / "bad"
            _plant(dest)
            raise OSError(errno.EACCES, "permission denied")
        _plant(Path(root) / str(case["case_id"]))
        return _meta(case, Path(root))

    rc, results = _run(
        tmp_path, [_case("bad"), _case("next")], monkeypatch, ["--dry-run"], _ok_child, replay=fake,
    )
    assert rc == 0
    rows = _read(results / "results.jsonl")
    assert [(row["case_id"], row["status"]) for row in rows] == [("bad", "replay_failed"), ("next", "ok")]
    assert "permission denied" in rows[0]["error"]
    assert not (tmp_path / "replay" / "bad" / ".venv").exists()
    assert not (tmp_path / "replay" / "next" / ".venv").exists()
    assert (tmp_path / "replay" / "bad" / "keep.txt").is_file()


def test_runtimeerror_without_enospc_text_is_replay_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(src: Path, case: dict, root: Path) -> dict:
        raise RuntimeError("disk full")

    rc, results = _run(tmp_path, [_case("bad")], monkeypatch, ["--dry-run"], _ok_child, replay=fake)
    assert rc == 0
    row = _read(results / "results.jsonl")[0]
    assert row["status"] == "replay_failed"
    assert "disk full" in row["error"]
    assert "No space left on device" not in row["error"]


@pytest.mark.parametrize("phase", ["replay", "venv", "child"])
def test_enospc_text_any_phase_stops_without_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], phase: str,
) -> None:
    seen: list[str] = []
    ran: list[str] = []

    def fake(src: Path, case: dict, root: Path) -> dict:
        seen.append(str(case["case_id"]))
        if phase == "replay" and case["case_id"] == "full":
            dest = Path(root) / "full"
            _plant(dest)
            raise RuntimeError("fatal: unable to write new index file: No space left on device")
        _plant(Path(root) / str(case["case_id"]))
        return _meta(case, Path(root))

    def make_venv(path: Path, timeout: int = 600) -> dict:
        if phase == "venv":
            raise RuntimeError("python3 -m venv: No space left on device")
        return {"ok": True, "installed": [], "failed": []}

    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        ran.append(Path(replay_path).name)
        if phase == "child":
            raise RuntimeError("git merge-tree failed: No space left on device")
        return {"status": "ok", "verdict": _verdict(0.0), "seconds": 0.01}

    monkeypatch.setattr("scripts.bench.run.make_venv", make_venv)
    rc, results = _run(
        tmp_path,
        [_case("full"), _case("later")],
        monkeypatch,
        ["--dry-run", "--venv"],
        child,
        replay=fake,
    )
    assert rc == 3
    assert seen == ["full"]
    assert ran == ([] if phase != "child" else ["full"])
    assert not (results / "results.jsonl").exists()
    assert not (results.parent / "spend.json").exists()
    assert not (tmp_path / "replay" / "full" / ".venv").exists()
    assert not (tmp_path / "replay" / "full" / ".mg-workspace").exists()
    err = capsys.readouterr().err
    assert "disk stop no space left on device case=full; result not recorded" in err
    assert "case=later" not in err


@pytest.mark.parametrize("mode", ["stderr", "verdict"])
def test_record_containing_enospc_text_is_not_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        name = Path(replay_path).name
        if name != "full":
            return {"status": "ok", "verdict": _verdict(0.1), "seconds": 0.01}
        if mode == "stderr":
            return {
                "status": "error",
                "stderr": "OSError: [Errno 28] No space left on device",
                "seconds": 0.2,
            }
        verdict = _verdict(0.4)
        verdict["fallback_reason"] = "No space left on device"
        return {"status": "ok", "verdict": verdict, "seconds": 0.2}

    rc, results = _run(
        tmp_path, [_case("ok"), _case("full"), _case("later")], monkeypatch, ["--dry-run"], child,
    )
    assert rc == 3
    rows = _read(results / "results.jsonl")
    assert [row["case_id"] for row in rows] == ["ok"]
    blob = (results / "results.jsonl").read_text(encoding="utf-8")
    assert "No space left on device" not in blob
    spend = json.loads((results.parent / "spend.json").read_text(encoding="utf-8"))
    assert spend["cases"] == 1
    assert spend["spent_usd"] == pytest.approx(0.1)
    err = capsys.readouterr().err
    assert "disk stop no space left on device case=full; result not recorded" in err
    assert "case=later" not in err


def test_case_workspace_symlinks_caches_and_keeps_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    parent = Path(settings.workspace_dir)
    cache = parent / "nemotron-cache"
    cache.mkdir()
    (cache / "hit.json").write_text("{}", encoding="utf-8")
    verify_cache = parent / "verify-cache"
    verify_cache.mkdir()
    (verify_cache / "x.py").write_text("x", encoding="utf-8")
    verify = parent / "verify"
    verify.mkdir()
    (verify / "marker").write_text("v", encoding="utf-8")
    (parent / "note.txt").write_text("n", encoding="utf-8")
    js = parent / "js-deps"
    js.mkdir()
    (js / "blob").write_text("parent-js", encoding="utf-8")
    external = tmp_path / "external-cache"
    external.mkdir()
    (external / "secret").write_text("survive", encoding="utf-8")
    (parent / "alias").symlink_to(external, target_is_directory=True)
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "wrong-workspace"))
    seen: dict[str, Path] = {}

    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        ws = Path(env["WORKSPACE_DIR"])
        seen["ws"] = ws
        assert ws == (Path(replay_path) / ".mg-workspace").resolve()
        assert ws.is_dir() and not ws.is_symlink()
        for name, target in (
            ("nemotron-cache", cache),
            ("verify-cache", verify_cache),
            ("verify", verify),
            ("alias", parent / "alias"),
        ):
            link = ws / name
            assert link.is_symlink()
            assert os.path.realpath(link) == os.path.realpath(target)
        assert (ws / "note.txt").is_symlink()
        assert (ws / "note.txt").read_text(encoding="utf-8") == "n"
        assert not (ws / "js-deps").exists()
        (ws / "js-deps" / "pkg").mkdir(parents=True)
        (ws / "js-deps" / "pkg" / "index.js").write_text("big", encoding="utf-8")
        (ws / "nemotron-cache" / "hit.json").write_text('{"ok":1}', encoding="utf-8")
        (ws / "alias" / "secret").write_text("survive-still", encoding="utf-8")
        return {"status": "ok", "verdict": _verdict(0.0), "seconds": 0.01}

    rc, results = _run(tmp_path, [_case("c1")], monkeypatch, ["--dry-run"], child)
    assert rc == 0
    assert seen["ws"] == (tmp_path / "replay" / "c1" / ".mg-workspace").resolve()
    assert not (tmp_path / "replay" / "c1" / ".mg-workspace").exists()
    assert (cache / "hit.json").read_text(encoding="utf-8") == '{"ok":1}'
    assert (verify_cache / "x.py").read_text(encoding="utf-8") == "x"
    assert (verify / "marker").read_text(encoding="utf-8") == "v"
    assert (parent / "note.txt").read_text(encoding="utf-8") == "n"
    assert (js / "blob").read_text(encoding="utf-8") == "parent-js"
    assert external.is_dir()
    assert (external / "secret").read_text(encoding="utf-8") == "survive-still"
    assert (parent / "alias").is_symlink()
    row = _read(results / "results.jsonl")[0]
    assert row["case_id"] == "c1"
    assert row["status"] == "ok"


def test_keep_venv_keeps_workspace_symlinks_and_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    parent = Path(settings.workspace_dir)
    cache = parent / "nemotron-cache"
    cache.mkdir()
    (cache / "hit.json").write_text("1", encoding="utf-8")
    (parent / "js-deps").mkdir()
    (parent / "js-deps" / "blob").write_text("parent-js", encoding="utf-8")

    def child(replay_path: Path, *, cwd: Path, env: dict, timeout: float, argv=None) -> dict:
        ws = Path(env["WORKSPACE_DIR"])
        assert (ws / "nemotron-cache").is_symlink()
        assert not (ws / "js-deps").exists()
        (ws / "js-deps").mkdir()
        (ws / "js-deps" / "local").write_text("case", encoding="utf-8")
        return {"status": "ok", "verdict": _verdict(0.0), "seconds": 0.01}

    rc, _results = _run(tmp_path, [_case("ok")], monkeypatch, ["--dry-run", "--keep-venv"], child)
    assert rc == 0
    ws = tmp_path / "replay" / "ok" / ".mg-workspace"
    assert ws.is_dir() and not ws.is_symlink()
    assert (ws / "nemotron-cache").is_symlink()
    assert os.path.realpath(ws / "nemotron-cache") == os.path.realpath(cache)
    assert (cache / "hit.json").read_text(encoding="utf-8") == "1"
    assert (ws / "js-deps" / "local").read_text(encoding="utf-8") == "case"
    assert (parent / "js-deps" / "blob").read_text(encoding="utf-8") == "parent-js"
    assert not (parent / "js-deps" / "local").exists()
