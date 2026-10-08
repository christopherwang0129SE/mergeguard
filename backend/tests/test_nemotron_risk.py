"""Nemotron refinement: mocked Token Factory, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.services import sentinel
from app.services.nemotron import (
    MAX_PROMPT_CHARS,
    NemotronError,
    assess_pair,
    parse_assessment,
)
from app.services.sentinel import combine_scores


def _pairs(repo: Path):
    res = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"])
    by_pair = {frozenset((p.branch_a, p.branch_b)): p for p in res.pairs}
    return res, by_pair


def test_combine_scores_keeps_textual_overlap() -> None:
    assert combine_scores(80, 10, overlapping_files=1) == 80
    assert combine_scores(10, 90, overlapping_files=0) == 50
    assert combine_scores(0, 0, overlapping_files=0) == 0.0
    blended = (0.5 * 47.5) + (0.5 * 90)
    assert combine_scores(47.5, 90, overlapping_files=1) == round(max(47.5, blended), 1)


def test_defaults_point_at_nemotron_on_token_factory() -> None:
    assert Settings.model_fields["nebius_model"].default == "nvidia/nemotron-3-super-120b-a12b"
    base_url = Settings.model_fields["nebius_base_url"].default
    assert base_url == "https://api.tokenfactory.us-central1.nebius.com/v1/"
    assert Settings.model_fields["nebius_enabled"].default is True
    assert Settings.model_fields["nebius_max_pairs"].default == 6


def test_parse_assessment_accepts_fenced_json_after_think() -> None:
    text = (
        "<think>they both touch app.py {not json}</think>\n"
        "```json\n"
        '{"risk_score": "80", "risk_level": "nope", "rationale": "Same function."}\n'
        "```"
    )
    got = parse_assessment(text)
    assert got.risk_score == 80.0
    assert got.rationale == "Same function."
    assert got.risk_level is None


def test_parse_assessment_rejects_prose() -> None:
    with pytest.raises(NemotronError) as exc:
        parse_assessment("these branches look risky")
    assert exc.value.code == "unparsable"


def test_model_assessment_is_used(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def fake_complete(prompt: str) -> str:
        prompts.append(prompt)
        return (
            "```json\n"
            + json.dumps(
                {
                    "risk_score": 90,
                    "risk_level": "high",
                    "rationale": "Both branches edit the same lines of app.py.",
                }
            )
            + "\n```"
        )

    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.nemotron.complete", fake_complete)

    res, by_pair = _pairs(repo)
    ab = by_pair[frozenset(("feat-a", "feat-b"))]
    ac = by_pair[frozenset(("feat-a", "feat-c"))]

    assert ab.score_source == "model"
    assert ab.fallback_reason is None
    assert ab.model_score == 90
    assert ab.model_level == "high"
    assert ab.model_rationale == "Both branches edit the same lines of app.py."
    assert ab.rationale == ab.model_rationale
    assert ab.git_score is not None
    assert ab.score == combine_scores(ab.git_score, 90, ab.overlapping_files)
    assert ab.score >= ab.git_score
    assert ab.level == "medium"
    assert ab.git_rationale is not None and "overlapping" in ab.git_rationale

    assert ac.score_source == "model"
    assert ac.overlapping_files == 0
    assert ac.git_score is not None
    assert ac.score == combine_scores(ac.git_score, 90, 0)
    assert ab.score > ac.score
    assert res.pairs[0].score == ab.score
    assert res.risk_model == "nvidia/nemotron-3-super-120b-a12b"

    assert prompts
    assert all(len(prompt) <= MAX_PROMPT_CHARS for prompt in prompts)
    joined = "\n".join(prompts)
    assert "feat-a" in joined and "feat-b" in joined
    assert "app.py" in joined
    assert "a edits top" in joined
    assert "overlapping=true" in joined


def test_model_failure_and_garbage_use_git_score(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")

    def explode(_prompt: str) -> str:
        raise TimeoutError("timed out")

    monkeypatch.setattr("app.services.nemotron.complete", explode)
    _res, failed = _pairs(repo)
    for pair in failed.values():
        assert pair.score_source == "git"
        assert pair.fallback_reason == "model_error"
        assert pair.score == pair.git_score
        assert pair.model_rationale is None
        assert pair.rationale == pair.git_rationale
    assert _res.risk_model is None

    def garbage(_prompt: str) -> str:
        return "not json, just a hunch"

    monkeypatch.setattr("app.services.nemotron.complete", garbage)
    _res, junk = _pairs(repo)
    for pair in junk.values():
        assert pair.score_source == "git"
        assert pair.fallback_reason == "unparsable"
        assert pair.score == pair.git_score
        assert pair.model_score is None
    assert _res.risk_model is None


def test_no_api_key_skips_the_model(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(_prompt: str) -> str:
        raise AssertionError("model should not be called without a key")

    monkeypatch.setattr(settings, "nebius_api_key", "   ")
    monkeypatch.setattr(settings, "nebius_enabled", True)
    monkeypatch.setattr("app.services.nemotron.complete", explode)

    res, by_pair = _pairs(repo)
    assert res.risk_model is None
    assert all(pair.score_source == "git" for pair in by_pair.values())
    assert all(pair.fallback_reason == "no_api_key" for pair in by_pair.values())
    assert all(pair.score == pair.git_score for pair in by_pair.values())


def test_disabled_setting_skips_the_model(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(_prompt: str) -> str:
        raise AssertionError("model should not be called when disabled")

    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr(settings, "nebius_enabled", False)
    monkeypatch.setattr("app.services.nemotron.complete", explode)

    _res, by_pair = _pairs(repo)
    assert all(pair.fallback_reason == "disabled" for pair in by_pair.values())
    assert all(pair.score_source == "git" for pair in by_pair.values())
    assert settings.nebius_ready is False


def test_pair_cap_sends_only_the_riskiest_pairs(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []

    def fake_complete(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(
            {"risk_score": 95, "risk_level": "high", "rationale": "Overlap on app.py."}
        )

    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr(settings, "nebius_max_pairs", 1)
    monkeypatch.setattr("app.services.nemotron.complete", fake_complete)

    _res, by_pair = _pairs(repo)
    ab = by_pair[frozenset(("feat-a", "feat-b"))]
    ac = by_pair[frozenset(("feat-a", "feat-c"))]
    bc = by_pair[frozenset(("feat-b", "feat-c"))]

    assert len(prompts) == 1
    assert "feat-a" in prompts[0] and "feat-b" in prompts[0]
    assert ab.score_source == "model"
    assert ac.fallback_reason == "pair_cap"
    assert bc.fallback_reason == "pair_cap"
    assert ac.score == ac.git_score
    assert bc.score == bc.git_score


def test_complete_uses_token_factory_chat_completions(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _Message:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = _Message(content)

    class _Completion:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **kwargs):
            captured["create"] = kwargs
            return _Completion(
                '{"risk_score": 12, "risk_level": "low", "rationale": "Independent edits."}'
            )

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _Client:
        def __init__(self, **kwargs) -> None:
            captured["init"] = kwargs
            self.chat = _Chat()

    monkeypatch.setattr(settings, "nebius_api_key", "test-key-not-real")
    monkeypatch.setattr("app.services.nemotron.OpenAI", _Client)

    got = assess_pair("pair context")
    assert got.risk_score == 12
    assert got.rationale == "Independent edits."
    assert captured["init"]["api_key"] == "test-key-not-real"
    assert captured["init"]["base_url"] == "https://api.tokenfactory.us-central1.nebius.com/v1/"
    assert captured["init"]["max_retries"] == 0
    assert captured["init"]["timeout"] == 40.0
    created = captured["create"]
    assert created["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert created["messages"][0]["role"] == "system"
    assert "JSON" in created["messages"][0]["content"]
    assert created["messages"][1] == {"role": "user", "content": "pair context"}
    assert created["max_tokens"] == 2048


def test_empty_model_response_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Completion:
        choices: list = []

    class _Completions:
        def create(self, **kwargs):
            return _Completion()

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, **kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(settings, "nebius_api_key", "test-key-not-real")
    monkeypatch.setattr("app.services.nemotron.OpenAI", _Client)

    with pytest.raises(NemotronError) as exc:
        assess_pair("pair context")
    assert exc.value.code == "model_error"


def test_analyze_api_keeps_existing_fields(repo: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    response = client.post(
        "/api/analyze",
        json={
            "repo_path": str(repo),
            "base_branch": "main",
            "branches": ["feat-a", "feat-b", "feat-c"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    pair = body["pairs"][0]
    for key in (
        "branch_a",
        "branch_b",
        "score",
        "level",
        "shared_files",
        "overlapping_files",
        "collisions",
        "rationale",
    ):
        assert key in pair
    assert pair["score_source"] == "git"
    assert pair["fallback_reason"] == "no_api_key"
    assert body["risk_model"] is None

    health = client.get("/api/health")
    assert health.status_code == 200
    health_body = health.json()
    assert health_body["status"] == "ok"
    assert health_body["nebius_ready"] is False
    assert health_body["nebius_model"] == "nvidia/nemotron-3-super-120b-a12b"
