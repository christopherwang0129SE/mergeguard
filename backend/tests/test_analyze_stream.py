"""NDJSON progress from POST /api/analyze/stream, and the progress callback."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.config import settings
from app.main import app
from app.schemas import AnalyzeResponse, BranchTests, Verification
from app.services import git_service, nemotron, sentinel, verifier
from app.services.nemotron import NemotronError


def _events(response) -> list[dict]:
    assert response.status_code == 200, response.text
    assert "application/x-ndjson" in response.headers["content-type"]
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _stage_pairs(events: list[dict]) -> list[tuple[str, str]]:
    return [(event["stage"], event["status"]) for event in events if event["type"] == "stage"]


def _stage_detail(events: list[dict], stage: str, status: str) -> str:
    return next(
        event["detail"]
        for event in events
        if event["type"] == "stage" and event["stage"] == stage and event["status"] == status
    )


def _without_elapsed(payload: dict) -> dict:
    usage = payload.get("usage")
    if isinstance(usage, dict):
        usage = dict(usage)
        usage.pop("elapsed_s", None)
        payload = {**payload, "usage": usage}
    return payload


def test_progress_callback_does_not_change_the_result(repo: Path) -> None:
    seen: list[dict] = []
    with_events = sentinel.analyze(
        repo,
        "main",
        ["feat-a", "feat-b", "feat-c"],
        progress=seen.append,
    )
    plain = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"])
    assert _without_elapsed(with_events.model_dump(mode="json")) == _without_elapsed(
        plain.model_dump(mode="json")
    )
    assert _stage_pairs(seen) == [
        ("diff", "start"),
        ("diff", "done"),
        ("prefilter", "start"),
        ("prefilter", "done"),
        ("model", "skipped"),
        ("verify", "skipped"),
        ("branch_tests", "skipped"),
    ]
    assert any(event["type"] == "pair" for event in seen)


def test_stream_matches_analyze_and_emits_stages_in_order(repo: Path) -> None:
    client = TestClient(app)
    payload = {
        "repo_path": str(repo),
        "base_branch": "main",
        "branches": ["feat-a", "feat-b", "feat-c"],
    }
    direct = client.post("/api/analyze", json=payload)
    assert direct.status_code == 200, direct.text
    streamed = client.post(
        "/api/analyze/stream",
        json=payload,
        headers={"Origin": "http://localhost:3000"},
    )
    assert streamed.headers["access-control-allow-origin"] == "http://localhost:3000"
    events = _events(streamed)
    assert events[-1]["type"] == "result"
    assert [event["type"] for event in events].count("result") == 1
    assert _stage_pairs(events) == [
        ("resolve", "start"),
        ("resolve", "done"),
        ("diff", "start"),
        ("diff", "done"),
        ("prefilter", "start"),
        ("prefilter", "done"),
        ("model", "skipped"),
        ("verify", "skipped"),
        ("branch_tests", "skipped"),
    ]
    for event in events:
        assert isinstance(event["t"], (int, float))
        assert event["t"] >= 0
    assert events[0]["t"] <= events[-1]["t"]

    branches = next(event for event in events if event["type"] == "branches")
    assert branches["base_branch"] == "main"
    assert branches["branches"] == ["feat-a", "feat-b", "feat-c"]

    latest: dict[tuple[str, str], dict] = {}
    for event in events:
        if event["type"] != "pair":
            continue
        pair = event["pair"]
        latest[(pair["branch_a"], pair["branch_b"])] = pair
    result = events[-1]["result"]
    result_pairs = {(pair["branch_a"], pair["branch_b"]): pair for pair in result["pairs"]}
    assert latest == result_pairs
    assert len(latest) == 3
    # Git score first, then the prefilter update, then the model-skip update.
    assert sum(1 for event in events if event["type"] == "pair") > len(latest)

    assert _without_elapsed(result) == _without_elapsed(direct.json())


def test_model_stage_runs_when_a_key_is_set(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "test-key-not-real")
    monkeypatch.setattr(settings, "verify_enabled", False)

    def fail_complete(*_args, **_kwargs):
        raise NemotronError("model_error", "offline")

    monkeypatch.setattr(nemotron, "complete", fail_complete)
    response = TestClient(app).post(
        "/api/analyze/stream",
        json={"repo_path": str(repo), "branches": ["feat-a", "feat-b"]},
    )
    events = _events(response)
    assert ("model", "start") in _stage_pairs(events)
    assert ("model", "done") in _stage_pairs(events)
    assert _stage_detail(events, "model", "done") == "0 pairs scored by the model"
    assert ("verify", "skipped") in _stage_pairs(events)
    pair = next(event["pair"] for event in events if event["type"] == "pair" and event["pair"].get("fallback_reason") == "model_error")
    assert pair["score_source"] == "git"


def test_stage_details_pluralize_scored_and_confirmed_pairs(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr(settings, "branch_tests_enabled", False)
    monkeypatch.setattr(
        nemotron,
        "complete",
        lambda _prompt: json.dumps(
            {"risk_score": 80, "risk_level": "high", "rationale": "These edits interact."}
        ),
    )
    monkeypatch.setattr(verifier, "verify_pair", lambda *_a, **_k: Verification(status="confirmed"))

    one: list[dict] = []
    sentinel.analyze(
        repo,
        "main",
        ["feat-a", "feat-b"],
        verify=True,
        verify_allowed=True,
        use_cache=False,
        progress=one.append,
    )
    assert _stage_detail(one, "model", "done") == "1 pair scored by the model"
    assert _stage_detail(one, "verify", "done") == "1 pair confirmed"

    many: list[dict] = []
    sentinel.analyze(
        repo,
        "main",
        ["feat-a", "feat-b", "feat-c"],
        verify=True,
        verify_allowed=True,
        use_cache=False,
        progress=many.append,
    )
    assert _stage_detail(many, "model", "done") == "3 pairs scored by the model"
    assert _stage_detail(many, "verify", "done") == "3 pairs confirmed"


def test_branch_test_stage_detail_pluralizes(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = {"status": "passed"}

    def fake_check(_repo, _base, _a, _b):
        return BranchTests(status=outcome["status"])

    monkeypatch.setattr("app.services.branch_tests.check_pair", fake_check)

    def run(branches: list[str]) -> str:
        events: list[dict] = []
        sentinel.analyze(
            repo,
            "main",
            branches,
            verify=True,
            verify_allowed=True,
            progress=events.append,
        )
        return _stage_detail(events, "branch_tests", "done")

    assert run(["feat-a", "feat-b"]) == "0 pairs confirmed"
    outcome["status"] = "confirmed"
    assert run(["feat-a", "feat-b"]) == "1 pair confirmed"
    assert run(["feat-a", "feat-b", "feat-c"]) == "3 pairs confirmed"


def test_branch_test_stage_follows_verify(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verifier, "DEMO_ROOT", repo.parent)
    response = TestClient(app).post(
        "/api/analyze/stream",
        json={"repo_path": str(repo), "branches": ["feat-a", "feat-b"], "verify": True},
    )
    events = _events(response)
    stages = _stage_pairs(events)
    assert stages.index(("verify", "skipped")) < stages.index(("branch_tests", "start"))
    assert ("branch_tests", "done") in stages
    assert events[-1]["type"] == "result"
    assert events[-1]["result"]["pairs"][0]["branch_tests"]["status"] == "skipped"


def test_preflight_errors_are_json_not_a_stream(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(app)
    missing = client.post("/api/analyze/stream", json={"repo_path": str(tmp_path / "nope")})
    assert missing.status_code == 400
    assert missing.headers["content-type"].startswith("application/json")
    assert "not a git repository" in missing.json()["detail"]

    only_main = tmp_path / "solo"
    only_main.mkdir()
    subprocess_git(only_main)
    empty = client.post("/api/analyze/stream", json={"repo_path": str(only_main)})
    assert empty.status_code == 400
    assert "No branches" in empty.json()["detail"]
    assert empty.headers["content-type"].startswith("application/json")

    def boom(*_args, **_kwargs):
        raise git_service.GitError("clone failed: no such host")

    monkeypatch.setattr(routes.git_service, "clone", boom)
    remote = client.post(
        "/api/analyze/stream",
        json={"repo_url": "https://example.com/nope.git"},
    )
    assert remote.status_code == 400
    assert remote.headers["content-type"].startswith("application/json")
    assert remote.json() == {"detail": "clone failed: no such host"}


def subprocess_git(path: Path) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"], check=True)


def test_demo_mode_rejects_the_stream_before_any_event(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(verifier, "DEMO_ROOT", tmp_path / "bundled")
    called = {"clone": 0}

    def clone(*_args, **_kwargs):
        called["clone"] += 1
        raise AssertionError("clone should not run")

    monkeypatch.setattr(routes.git_service, "clone", clone)
    client = TestClient(app)
    other = client.post("/api/analyze/stream", json={"repo_path": str(repo)})
    assert other.status_code == 403
    assert "not allowed" in other.json()["detail"]
    remote = client.post(
        "/api/analyze/stream",
        json={"repo_url": "https://github.com/example/huge.git"},
    )
    assert remote.status_code == 403
    assert "repo_url" in remote.json()["detail"]
    assert called["clone"] == 0


def test_mid_stream_git_error_is_the_last_line(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise git_service.GitError("diff exploded")

    monkeypatch.setattr(routes.sentinel, "analyze", boom)
    events = _events(
        TestClient(app).post("/api/analyze/stream", json={"repo_path": str(repo), "branches": ["feat-a"]})
    )
    assert events[-1]["type"] == "error"
    assert events[-1]["detail"] == "diff exploded"
    assert all(event["type"] != "result" for event in events)
    assert events[0]["type"] == "stage"


def test_heartbeat_while_analyze_is_quiet(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_HEARTBEAT_SECONDS", 0.05)
    canned = AnalyzeResponse(base_branch="main", branches=["feat-a", "feat-b"], diffs=[], pairs=[])

    def slow(*_args, **_kwargs):
        time.sleep(0.35)
        return canned

    monkeypatch.setattr(routes.sentinel, "analyze", slow)
    events = _events(
        TestClient(app).post(
            "/api/analyze/stream",
            json={"repo_path": str(repo), "branches": ["feat-a", "feat-b"]},
        )
    )
    assert any(event["type"] == "ping" for event in events)
    assert events[-1]["type"] == "result"
    assert events[-1]["result"]["branches"] == ["feat-a", "feat-b"]


def test_cors_preflight_allows_the_stream() -> None:
    response = TestClient(app).options(
        "/api/analyze/stream",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]
