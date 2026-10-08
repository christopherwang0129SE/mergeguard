"""Cost-aware pipeline: static prefilter, top-K, disk cache, token/$ accounting."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.services import sentinel
from app.services.nemotron import LLMText, estimate_cost_usd
from app.services.prefilter import file_symbols, regex_symbols, symbol_overlap


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


LIB = '''def f(x):
    return x + 1


def g(x):
    return x * 2
'''

APP = '''from lib import g


def run():
    return g(3)
'''


@pytest.fixture()
def symrepo(tmp_path: Path) -> Path:
    r = tmp_path / "sym"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "lib.py").write_text(LIB)
    (r / "app.py").write_text(APP)
    (r / "README.md").write_text("# sym\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")

    # feat-def: changes the body of f in lib.py
    _git(r, "checkout", "-q", "-b", "feat-def")
    (r / "lib.py").write_text(LIB.replace("return x + 1", "return x + 100"))
    _git(r, "commit", "-qam", "f adds 100")

    # feat-call: starts calling f from app.py (different file)
    _git(r, "checkout", "-q", "main")
    _git(r, "checkout", "-q", "-b", "feat-call")
    (r / "app.py").write_text(
        APP.replace("from lib import g", "from lib import f, g").replace(
            "return g(3)", "return g(f(3))"
        )
    )
    _git(r, "commit", "-qam", "run uses f")

    # docs: harmless
    _git(r, "checkout", "-q", "main")
    _git(r, "checkout", "-q", "-b", "docs")
    (r / "README.md").write_text("# sym\n\nMore docs.\n")
    _git(r, "commit", "-qam", "docs")
    _git(r, "checkout", "-q", "main")
    return r


BRANCHES = ["feat-def", "feat-call", "docs"]


def _fake(calls: list[str], score: float = 80):
    def fake_complete(prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        body = json.dumps(
            {"risk_score": score, "risk_level": "high", "rationale": "f changes under a caller."}
        )
        return LLMText(body, prompt_tokens=1000, completion_tokens=200)

    return fake_complete


def _by_pair(res):
    return {frozenset((p.branch_a, p.branch_b)): p for p in res.pairs}


def test_python_symbols_def_and_call() -> None:
    changed = file_symbols("lib.py", LIB.replace("x + 1", "x + 100"), [(2, 2)])
    assert changed.defs == {"f"}
    caller_src = APP.replace("return g(3)", "return g(f(3))")
    caller = file_symbols("app.py", caller_src, [(5, 5)])
    assert {"f", "g"} <= caller.calls
    assert "run" in caller.defs
    assert symbol_overlap(changed, caller) == ("f",)


def test_regex_heuristic_for_other_languages() -> None:
    js = "export function price(a) {\n  return a * 2;\n}\nconst total = (x) => price(x) + 1;\n"
    syms = regex_symbols(js, [(2, 2)])
    assert "price" in syms.defs
    caller = regex_symbols(js, [(4, 4)])
    assert "price" in caller.calls
    assert "total" in caller.defs
    assert "if" not in regex_symbols("if (x) { go(1) }\n", [(1, 1)]).calls


def test_prefilter_drops_harmless_pairs_before_the_llm(
    symrepo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.nemotron.complete", _fake(calls))

    res = sentinel.analyze(symrepo, "main", BRANCHES)
    pairs = _by_pair(res)
    defcall = pairs[frozenset(("feat-def", "feat-call"))]
    assert defcall.shared_files == 0
    assert defcall.symbol_overlap == ["f"]
    assert defcall.prefilter_candidate is True
    assert defcall.score_source == "model"
    assert "Static prefilter" in calls[0] and ": f" in calls[0]

    for other in ("feat-def", "feat-call"):
        docs = pairs[frozenset(("docs", other))]
        assert docs.prefilter_candidate is False
        assert docs.fallback_reason == "prefiltered"
        assert docs.score_source == "git"
        assert docs.prompt_tokens == 0

    # Exactly one LLM call: the two docs pairs never reached the model.
    assert len(calls) == 1
    assert res.usage is not None
    assert res.usage.llm_calls == 1
    assert res.usage.pairs_prefiltered == 2
    assert res.usage.pairs_sent_to_model == 1
    assert res.usage.prompt_tokens == 1000
    assert res.usage.completion_tokens == 200
    assert res.usage.total_tokens == 1200
    assert res.usage.estimated_cost_usd == pytest.approx(0.0003 + 0.00018)


def test_second_identical_run_makes_zero_llm_calls(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.nemotron.complete", _fake(calls))

    first = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"])
    assert first.usage is not None and first.usage.llm_calls == 3
    assert len(calls) == 3
    assert first.usage.total_tokens == 3600

    second = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"])
    assert len(calls) == 3  # no new calls
    assert second.usage is not None
    assert second.usage.llm_calls == 0
    assert second.usage.cache_hits == 3
    assert second.usage.total_tokens == 0
    assert second.usage.estimated_cost_usd == 0
    assert all(p.model_cached for p in second.pairs)
    assert [(p.branch_a, p.branch_b, p.score) for p in first.pairs] == [
        (p.branch_a, p.branch_b, p.score) for p in second.pairs
    ]

    # Opting out of the cache calls the model again.
    third = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"], use_cache=False)
    assert third.usage is not None and third.usage.llm_calls == 3
    assert len(calls) == 6


def test_cache_key_changes_with_head_sha(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.nemotron.complete", _fake(calls))
    sentinel.analyze(repo, "main", ["feat-a", "feat-b"])
    assert len(calls) == 1
    _git(repo, "checkout", "-q", "feat-a")
    (repo / "app.py").write_text((repo / "app.py").read_text() + "line 21\n")
    _git(repo, "commit", "-qam", "more")
    _git(repo, "checkout", "-q", "main")
    sentinel.analyze(repo, "main", ["feat-a", "feat-b"])
    assert len(calls) == 2


def test_failed_calls_are_not_cached(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def garbage(prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        return LLMText("not json", prompt_tokens=50, completion_tokens=5)

    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.nemotron.complete", garbage)
    first = sentinel.analyze(repo, "main", ["feat-a", "feat-b"])
    assert first.usage is not None and first.usage.llm_calls == 1
    assert first.usage.prompt_tokens == 50  # tokens are counted even when unparsable
    sentinel.analyze(repo, "main", ["feat-a", "feat-b"])
    assert len(calls) == 2


def test_price_constants_and_estimate() -> None:
    assert Settings.model_fields["nemotron_input_usd_per_1m"].default == 0.30
    assert Settings.model_fields["nemotron_output_usd_per_1m"].default == 0.90
    assert estimate_cost_usd(1_000_000, 0) == pytest.approx(0.30)
    assert estimate_cost_usd(0, 1_000_000) == pytest.approx(0.90)


def test_api_returns_usage(repo: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).post(
        "/api/analyze",
        json={"repo_path": str(repo), "branches": ["feat-a", "feat-b", "feat-c"]},
    ).json()
    usage = body["usage"]
    assert usage["llm_calls"] == 0
    assert usage["estimated_cost_usd"] == 0
    assert usage["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert usage["top_k"] == 5
    assert usage["input_usd_per_1m"] == 0.30
