#!/usr/bin/env python3
"""Budgeted, resumable per-case MergeGuard bench.

    python scripts/bench/run.py --cases A.jsonl [--cases B.jsonl ...] \\
        --src-root SRC --replay-root REPLAY --results RESULTS/<run> \\
        [--venv] [--keep-venv] [--min-free-gb 5] [--verify/--no-verify] \\
        [--dry-run] [--max-usd 9] [--limit N] [--timeout 600]

One child per case (``python -m scripts.bench.run --child PATH``). The Token
Factory key comes only from ``TOKEN_FACTORY_API_KEY_BENCH`` (last whitespace
token). Spend is ``<results>/../spend.json``. The child's ``WORKSPACE_DIR`` is
``<replay_dir>/.mg-workspace`` (symlinks to the parent workspace except
``js-deps``, removed with the case venv unless ``--keep-venv``).
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts.bench.replay import build_replay, make_venv

_MIN_RESERVE = 0.05
_GB = 1024 ** 3  # same units as df -h
_MIN_FREE_GB = 5.0
_VERIFY_ENV = "MG_BENCH_VERIFY"
_WORKSPACE_ENV = "WORKSPACE_DIR"  # app.config.Settings.workspace_dir
_CASE_WORKSPACE = ".mg-workspace"
_SKIP_WORKSPACE_LINK = "js-deps"
_ENOSPC_TEXT = "No space left on device"
_FALSE = {"0", "false", "no", "off"}
_VERDICT_KEYS = (
    "level", "score", "score_source", "fallback_reason", "model_score",
    "verification", "branch_tests", "docs_sources", "rationale", "usage",
    "finding", "app_textual_conflict", "git_score", "model_level",
    "shared_files", "overlapping_files", "symbol_overlap", "static_breaks",
    "prefilter_candidate",
)


def _token(raw: str | None) -> str:
    parts = (raw or "").split()
    return parts[-1] if parts else ""


def child_env(
    base_env: Mapping[str, str], replay_root: Path | str | None = None, *, dry_run: bool = False,
) -> dict[str, str]:
    """Child env. Empty ``NEBIUS_API_KEY`` blocks pydantic-settings from reading ``.env``."""
    env = {str(key): str(value) for key, value in base_env.items()}
    token = _token(env.get("TOKEN_FACTORY_API_KEY_BENCH"))
    env["NEBIUS_API_KEY"] = token
    env["NEBIUS_ENABLED"] = "false" if dry_run or not token else "true"
    if replay_root is not None:
        env["VERIFY_ALLOWED_ROOTS"] = str(Path(replay_root).resolve())
    return env


def _status(verdict: dict, key: str):
    return (verdict.get(key) or {}).get("status")


def is_flagged(verdict: dict) -> bool:
    # A pure textual conflict is its own category, not a semantic finding.
    # Verdicts with no ``finding`` (older results) keep the previous rule.
    if verdict.get("finding") == "textual_conflict":
        return False
    high = verdict.get("level") == "high"
    return high or _status(verdict, "verification") == "confirmed" or _status(verdict, "branch_tests") == "confirmed"


def is_confirmed(verdict: dict) -> bool:
    return _status(verdict, "verification") == "confirmed" or _status(verdict, "branch_tests") == "confirmed"


def _src_repo(case: dict, src_root: Path) -> Path:
    text = str(case.get("repo") or "").strip().removesuffix(".git")
    if text.startswith("git@"):
        text = text.split(":", 1)[-1]
    elif "://" in text:
        text = text.split("://", 1)[1].split("/", 1)[-1]
    parts = [part for part in text.replace("\\", "/").split("/") if part]
    name = f"{parts[-2]}__{parts[-1]}" if len(parts) >= 2 else (parts[-1] if parts else text)
    return Path(src_root) / name


def _view(obj: object, *, attempts: bool) -> dict:
    if obj is None:
        view = {"status": None, "reason": ""}
    else:
        view = {"status": getattr(obj, "status", None), "reason": getattr(obj, "reason", "") or ""}
    if attempts:
        view["attempts"] = 0 if obj is None else int(getattr(obj, "attempts", 0) or 0)
    return view


def analyze_case(replay_path: Path, verify: bool | None = None) -> dict:
    """Score branches ``a`` and ``b``. The child prints this as one JSON line."""
    from app.services.sentinel import analyze
    from app.services.verifier import repo_allowed

    if verify is None:
        verify = os.environ.get(_VERIFY_ENV, "1").strip().lower() not in _FALSE
    repo = Path(replay_path)
    response = analyze(
        repo, "main", ["a", "b"], use_cache=True, verify=verify, verify_allowed=repo_allowed(repo),
    )
    if not response.pairs:
        raise RuntimeError("analyze returned no pairs")
    pair, usage = response.pairs[0], response.usage
    return {
        "level": pair.level, "score": pair.score, "score_source": pair.score_source,
        "fallback_reason": pair.fallback_reason, "model_score": pair.model_score,
        "verification": _view(pair.verification, attempts=True),
        "branch_tests": _view(pair.branch_tests, attempts=False),
        "docs_sources": len(pair.docs_sources or []),
        "rationale": str(pair.rationale or "")[:300],
        "usage": {
            "llm_calls": int(usage.llm_calls) if usage else 0,
            "total_tokens": int(usage.total_tokens) if usage else 0,
            "estimated_cost_usd": float(usage.estimated_cost_usd) if usage else 0.0,
        },
        "finding": pair.finding,
        "app_textual_conflict": pair.textual_conflict,
        "git_score": pair.git_score,
        "model_level": pair.model_level,
        "shared_files": pair.shared_files,
        "overlapping_files": pair.overlapping_files,
        "symbol_overlap": len(pair.symbol_overlap),
        "static_breaks": len(pair.static_breaks),
        "prefilter_candidate": pair.prefilter_candidate,
    }


def _secrets(env: Mapping[str, str]) -> list[str]:
    raw = (env.get("TOKEN_FACTORY_API_KEY_BENCH") or "").strip()
    values = [raw, _token(raw), (env.get("NEBIUS_API_KEY") or "").strip()]
    return [value for value in dict.fromkeys(values) if len(value) >= 4]


def _scrub(text: str, secrets: list[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def _scrub_obj(obj: object, secrets: list[str]) -> object:
    if isinstance(obj, str):
        return _scrub(obj, secrets)
    if isinstance(obj, dict):
        return {key: _scrub_obj(value, secrets) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_scrub_obj(item, secrets) for item in obj]
    return obj


def _tail(text: str, secrets: list[str]) -> str:
    return _scrub(text or "", secrets).strip()[-400:]


def _child_argv(replay_path: Path) -> list[str]:
    return [sys.executable, "-m", "scripts.bench.run", "--child", str(replay_path)]


def _kill_group(proc: subprocess.Popen) -> None:
    # ``start_new_session`` made the child its own group, so pytest/git die with it.
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_child(
    replay_path: Path, *, cwd: Path, env: Mapping[str, str], timeout: float, argv: list[str] | None = None,
) -> dict:
    """Run ``analyze_case`` in a child. Timeout kills that process group."""
    secrets, started = _secrets(env), time.monotonic()

    def finish(status: str, **extra: object) -> dict:
        return {"status": status, "seconds": round(time.monotonic() - started, 3), **extra}

    try:
        proc = subprocess.Popen(
            argv or _child_argv(replay_path), cwd=str(cwd),
            env={str(key): str(value) for key, value in env.items()},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", start_new_session=True,
        )
    except OSError as exc:
        return finish("error", stderr=_tail(f"{exc.__class__.__name__}: {exc}", secrets))
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return finish("timeout")
    detail = stderr or stdout or f"exit {proc.returncode}"
    if proc.returncode != 0:
        return finish("error", stderr=_tail(detail, secrets))
    line = next((item.strip() for item in reversed((stdout or "").splitlines()) if item.strip()), "")
    try:
        verdict = json.loads(line)
    except json.JSONDecodeError:
        return finish("error", stderr=_tail(detail or "no json", secrets))
    if not isinstance(verdict, dict):
        return finish("error", stderr=_tail("verdict was not an object", secrets))
    return finish("ok", verdict=verdict)


def _rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        rows.extend(json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def _done(path: Path) -> set[str]:
    found: set[str] = set()
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            found.add(str(json.loads(line)["case_id"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return found


def _spend(path: Path) -> dict:
    if not path.is_file():
        return {"spent_usd": 0.0, "cases": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"spent_usd": float(data.get("spent_usd") or 0.0), "cases": int(data.get("cases") or 0)}


def _over(spend: dict, max_usd: float) -> bool:
    cases, spent = int(spend["cases"]), float(spend["spent_usd"])
    return spent + max(_MIN_RESERVE, (spent / cases) if cases else 0.0) > max_usd


def _side(change: object) -> dict:
    src = change if isinstance(change, dict) else {}
    return {"sha": src.get("sha"), "subject": src.get("subject"), "pr": src.get("pr")}


def _record(case: dict, meta: dict | None, venv_ok, status: str, seconds: float, cost: float) -> dict:
    record = {
        "case_id": case.get("case_id"), "repo": case.get("repo"), "label": case.get("label"),
        "gt_kind": case.get("gt_kind"), "confidence": case.get("confidence"),
        "a": _side(case.get("a")), "b": _side(case.get("b")),
        "fix_sha": case.get("fix_sha"), "fix_subject": case.get("fix_subject"),
        "textual_conflict": None if meta is None else bool(meta.get("textual_conflict")),
        "a_apply_failed": None if meta is None else bool(meta.get("a_apply_failed")),
        "b_apply_failed": None if meta is None else bool(meta.get("b_apply_failed")),
        "venv_ok": venv_ok, "status": status, "seconds": round(seconds, 3), "cost": round(cost, 6),
    }
    if "parallel_evidence" in case:
        record["parallel_evidence"] = case["parallel_evidence"]
    return record


def _record_line(record: Mapping, secrets: list[str]) -> str:
    return json.dumps(_scrub_obj(record, secrets), ensure_ascii=False)


def _save(results: Path, spend_path: Path, spend: dict, record: dict, secrets: list[str]) -> dict:
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("a", encoding="utf-8") as handle:
        handle.write(_record_line(record, secrets) + "\n")
    spend = {
        "spent_usd": round(float(spend["spent_usd"]) + max(0.0, float(record.get("cost") or 0)), 6),
        "cases": int(spend["cases"]) + 1,
    }
    spend_path.parent.mkdir(parents=True, exist_ok=True)
    spend_path.write_text(json.dumps(spend) + "\n", encoding="utf-8")
    return spend


def _cost(outcome: dict) -> float:
    if outcome.get("status") != "ok":
        return 0.0
    try:
        return max(0.0, float(((outcome.get("verdict") or {}).get("usage") or {}).get("estimated_cost_usd") or 0))
    except (TypeError, ValueError):
        return 0.0


def _enospc(exc: BaseException) -> bool:
    """ENOSPC errno, or the phrase anywhere in ``str(exc)`` (git raises RuntimeError)."""
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return True
    return _ENOSPC_TEXT in str(exc)


def _record_enospc(record: Mapping, secrets: list[str]) -> bool:
    """True when the line about to be appended contains an out-of-space marker."""
    return _ENOSPC_TEXT in _record_line(record, secrets)


def _disk_probe(path: Path) -> Path:
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe


def _free_bytes(path: Path) -> int:
    return int(shutil.disk_usage(_disk_probe(path)).free)


def _case_replay_dir(replay_root: Path, case: Mapping) -> Path | None:
    case_id = str(case.get("case_id") or "")
    if not case_id or case_id in {".", ".."} or "/" in case_id or "\\" in case_id:
        return None
    return Path(replay_root) / case_id


def _inside_replay_root(replay_root: Path, replay_dir: Path | None) -> Path | None:
    """Resolved case directory when it is a direct child of ``replay_root``."""
    if replay_dir is None:
        return None
    root = Path(replay_root).resolve()
    try:
        directory = Path(replay_dir).resolve()
        directory.relative_to(root)
    except (OSError, ValueError):
        return None
    # Direct child only: ``resolve`` already dropped ``..`` and symlinks that leave the root.
    if directory == root or directory.parent != root:
        return None
    return directory


def _drop_inside(replay_root: Path, replay_dir: Path | None, name: str) -> None:
    directory = _inside_replay_root(replay_root, replay_dir)
    if directory is None:
        return
    # ``rmtree`` unlinks symlinks; it does not delete their targets.
    shutil.rmtree(directory / name, ignore_errors=True)


def _drop_venv(replay_root: Path, replay_dir: Path | None) -> None:
    """Remove ``<replay_dir>/.venv`` only when that directory is inside ``replay_root``."""
    _drop_inside(replay_root, replay_dir, ".venv")


def _parent_workspace() -> Path:
    from app.config import settings

    return Path(settings.workspace_dir).resolve()


def _prepare_case_workspace(replay_root: Path, replay_dir: Path | None) -> Path | None:
    """``<replay_dir>/.mg-workspace`` with parent-workspace symlinks except ``js-deps``.

    Analysis caches (``nemotron-cache``, ``verify-cache``, ``verify``, …) keep
    working. ``js-deps`` is created by the child inside this directory.
    """
    directory = _inside_replay_root(replay_root, replay_dir)
    if directory is None:
        return None
    workspace = directory / _CASE_WORKSPACE
    workspace.mkdir(parents=True, exist_ok=True)
    parent = _parent_workspace()
    if parent.is_dir():
        for entry in parent.iterdir():
            if entry.name == _SKIP_WORKSPACE_LINK:
                continue
            link = workspace / entry.name
            if link.is_symlink() or link.exists():
                continue
            link.symlink_to(entry, target_is_directory=entry.is_dir())
    return workspace


def _drop_case_workspace(replay_root: Path, replay_dir: Path | None) -> None:
    """Remove ``<replay_dir>/.mg-workspace`` only when that directory is inside ``replay_root``."""
    _drop_inside(replay_root, replay_dir, _CASE_WORKSPACE)


def _disk_stop(message: str) -> int:
    print(message, file=sys.stderr)
    return 3


def _stop_enospc(case_id: str) -> int:
    return _disk_stop(
        f"disk stop no space left on device case={case_id or '?'}; result not recorded",
    )


def run_bench(
    cases_paths: list[Path], src_root: Path, replay_root: Path, results_dir: Path, *,
    use_venv: bool = False, verify: bool = True, dry_run: bool = False, max_usd: float = 9.0,
    limit: int | None = None, timeout: float = 600.0, base_env: Mapping[str, str] | None = None,
    keep_venv: bool = False, min_free_gb: float = _MIN_FREE_GB,
) -> int:
    env_src = os.environ if base_env is None else base_env
    secrets = _secrets(env_src)
    results, spend_path = Path(results_dir) / "results.jsonl", Path(results_dir).parent / "spend.json"
    done, spend, started_n = _done(results), _spend(spend_path), 0
    for case in _rows(cases_paths):
        case_id = str(case.get("case_id") or "")
        if case_id and case_id in done:
            continue
        if limit is not None and started_n >= limit:
            break
        if _over(spend, max_usd):
            print(f"budget stop spent={float(spend['spent_usd']):.4f} max={max_usd:.4f}", file=sys.stderr)
            break
        free_gb = _free_bytes(replay_root) / _GB
        if free_gb < min_free_gb:
            return _disk_stop(
                f"disk stop free={free_gb:.2f}GB below min={min_free_gb:g}GB "
                f"case={case_id or '?'}; result not recorded",
            )
        started_n += 1
        t0, meta, venv_ok, failed = time.monotonic(), None, None, ""
        replay_dir = _case_replay_dir(replay_root, case)
        try:
            try:
                meta = build_replay(_src_repo(case, src_root), case, replay_root)
            except Exception as exc:
                if _enospc(exc):
                    return _stop_enospc(case_id)
                failed = _tail(f"{exc.__class__.__name__}: {exc}", secrets)
            else:
                reported = meta.get("path") if isinstance(meta, dict) else None
                if reported:
                    replay_dir = Path(str(reported))
            empty = bool((meta or {}).get("a_empty") or (meta or {}).get("b_empty"))
            if failed or not (meta or {}).get("b") or empty:
                record = _record(case, meta, None, "replay_failed", time.monotonic() - t0, 0.0)
                if failed:
                    record["error"] = failed
                if _record_enospc(record, secrets):
                    return _stop_enospc(case_id)
                spend = _save(results, spend_path, spend, record, secrets)
                done.add(case_id)
                print(f"{case_id} replay_failed", file=sys.stderr)
                continue
            if use_venv:
                try:
                    venv_ok = bool(make_venv(Path(meta["path"])).get("ok"))
                except Exception as exc:
                    if _enospc(exc):
                        return _stop_enospc(case_id)
                    venv_ok = False
            try:
                workspace = _prepare_case_workspace(replay_root, replay_dir)
            except Exception as exc:
                if _enospc(exc):
                    return _stop_enospc(case_id)
                raise
            child_env_map = child_env(env_src, replay_root, dry_run=dry_run)
            child_env_map[_VERIFY_ENV] = "1" if verify else "0"
            if workspace is not None:
                child_env_map[_WORKSPACE_ENV] = str(workspace)
            try:
                outcome = run_child(Path(meta["path"]).resolve(), cwd=_BACKEND, env=child_env_map, timeout=timeout)
            except Exception as exc:
                if _enospc(exc):
                    return _stop_enospc(case_id)
                outcome = {"status": "error", "seconds": round(time.monotonic() - t0, 3), "stderr": str(exc)}
            status, cost = str(outcome.get("status") or "error"), _cost(outcome)
            record = _record(case, meta, venv_ok, status, float(outcome.get("seconds") or 0), cost)
            if status == "ok":
                verdict = outcome.get("verdict") or {}
                record.update({key: verdict.get(key) for key in _VERDICT_KEYS})
                record["flagged"], record["confirmed"] = is_flagged(verdict), is_confirmed(verdict)
            elif status == "error":
                record["error"] = _tail(str(outcome.get("stderr") or ""), secrets)
            if _record_enospc(record, secrets):
                return _stop_enospc(case_id)
            spend = _save(results, spend_path, spend, record, secrets)
            done.add(case_id)
            print(f"{case_id} {status} {cost:.4f}", file=sys.stderr)
        finally:
            if not keep_venv:
                _drop_venv(replay_root, replay_dir)
                _drop_case_workspace(replay_root, replay_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, action="append", default=None)
    parser.add_argument("--src-root", type=Path)
    parser.add_argument("--replay-root", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--venv", action="store_true")
    parser.add_argument("--keep-venv", action="store_true")
    parser.add_argument("--min-free-gb", type=float, default=_MIN_FREE_GB)
    parser.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-usd", type=float, default=9.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--child", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.child is not None:
        sys.stdout.write(json.dumps(analyze_case(args.child), ensure_ascii=False) + "\n")
        return 0
    if not (args.cases and args.src_root and args.replay_root and args.results):
        parser.error("--cases, --src-root, --replay-root, and --results are required")
    return run_bench(
        args.cases, args.src_root, args.replay_root, args.results, use_venv=args.venv,
        verify=args.verify, dry_run=args.dry_run, max_usd=args.max_usd, limit=args.limit, timeout=args.timeout,
        keep_venv=args.keep_venv, min_free_gb=args.min_free_gb,
    )


if __name__ == "__main__":
    raise SystemExit(main())
