"""Model rationale vs. exact static signature checks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.services import claims, sentinel
from app.services.nemotron import LLMText

COMPAT = [
    "create_session: breaking signature change (removed session_context), but all 2 call "
    "site(s) in packages/adapters/discord/daimon/adapters/discord/bot.py on pr-17 match the "
    "new signature"
]

# Verbatim rationale from a live run on demo/real-case-1 (before this check).
LIVE = (
    "pr-14 adds required fields to PurgePreview and removes the session_context parameter "
    "from create_session, while pr-17's test code and bot.py still call these functions with "
    "the old signatures. This will cause the tests to fail and the bot to raise TypeError "
    "when creating sessions, representing a significant semantic conflict despite no file "
    "overlap."
)


def test_flags_a_signature_break_claim_the_static_check_disproves() -> None:
    assert claims.unsupported_claims(LIVE, COMPAT) == COMPAT


@pytest.mark.parametrize(
    "text",
    [
        "pr-14 adds required fields to PurgePreview; pr-17's test_branding.py still builds it "
        "without them, so the merge raises a ValidationError.",
        "PurgePreview gains required fields the new test does not pass; the create_session "
        "change is compatible with every caller.",
        "create_session now logs more.",  # mentioned, but no signature-break claim
    ],
)
def test_accurate_rationales_are_not_flagged(text: str) -> None:
    assert claims.unsupported_claims(text, COMPAT) == []


def test_refine_runs_one_repair_round_on_a_contradicted_rationale(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr(
        sentinel, "_with_static_checks",
        lambda _r, _b, p: p.model_copy(update={"static_compatible": COMPAT}),
    )
    prompts: list[str] = []
    replies = [
        {"risk_score": 85, "risk_level": "high", "rationale": LIVE},
        {"risk_score": 80, "risk_level": "high",
         "rationale": "pr-17's test still builds PurgePreview without the new required fields."},
    ]

    def fake(prompt: str, **_kw) -> str:
        prompts.append(prompt)
        body = replies[0] if "== Your previous answer ==" not in prompt else replies[1]
        return LLMText(json.dumps(body), 1000, 100)

    monkeypatch.setattr("app.services.nemotron.complete", fake)
    res = sentinel.analyze(repo, "main", ["feat-a", "feat-b"], use_cache=False)
    pair = res.pairs[0]
    assert "Static signature checks (exact):" in prompts[0]
    assert "- compatible: create_session" in prompts[0]
    assert len(prompts) == 2 and "every call site in the other branch" in prompts[1]
    assert pair.model_score == 80 and "create_session" not in pair.rationale
    assert pair.unsupported_claims == []
    assert (pair.prompt_tokens, pair.completion_tokens) == (2000, 200)
    assert res.usage.llm_calls == 2


def test_claims_that_survive_the_repair_round_are_marked(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr(
        sentinel, "_with_static_checks",
        lambda _r, _b, p: p.model_copy(update={"static_compatible": COMPAT}),
    )
    body = json.dumps({"risk_score": 85, "risk_level": "high", "rationale": LIVE})
    monkeypatch.setattr("app.services.nemotron.complete", lambda p, **_k: LLMText(body, 10, 5))
    pair = sentinel.analyze(repo, "main", ["feat-a", "feat-b"], use_cache=False).pairs[0]
    assert pair.unsupported_claims == COMPAT


def test_compatible_change_described_correctly_is_not_flagged() -> None:
    # Verbatim rationale from a live run on demo/harmless-demo.
    text = (
        "The area function receives an optional scale parameter with a default of 1, keeping "
        "existing calls valid; feat/summary calls area(w, h) which matches the new signature, "
        "and the branches modify separate files with no textual overlap."
    )
    compat = ["area: compatible signature change (new optional scale), but all 2 call site(s) "
              "in report.py on feat/summary match the new signature"]
    assert claims.unsupported_claims(text, compat) == []
