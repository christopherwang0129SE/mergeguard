"""Cost-aware pipeline: static prefilter, top-K, disk cache, token/$ accounting."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.services import sentinel
from app.services.nemotron import LLMText, estimate_cost_usd
from app.schemas import BranchDiff
from app.services import git_service
from app.services.prefilter import (
    branch_symbols,
    file_symbols,
    regex_symbols,
    symbol_overlap,
)


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
    assert changed.def_names == {"f"}
    caller_src = APP.replace("from lib import g", "from lib import f, g").replace(
        "return g(3)", "return g(f(3))"
    )
    caller = file_symbols("app.py", caller_src, [(1, 1), (5, 5)])
    assert {"f", "g"} <= caller.call_names
    assert {c.module for c in caller.calls if c.name in ("f", "g")} == {"lib"}
    assert "run" in caller.def_names
    names, evidence = symbol_overlap(changed, caller, "feat-def", "feat-call")
    assert names == ("f",)
    assert evidence == ("f: changed by feat-def (lib.py), called by feat-call (app.py)",)


def test_calls_to_a_function_neither_branch_changed_do_not_count() -> None:
    # Both branches call g(); neither changes g. No overlap.
    a = file_symbols("app.py", "from lib import g\n\n\ndef run():\n    return g(1)\n", [(5, 5)])
    b = file_symbols("cli.py", "from lib import g\n\n\ndef main2():\n    return g(2)\n", [(5, 5)])
    assert "g" in a.call_names and "g" in b.call_names
    assert symbol_overlap(a, b) == ((), ())


def test_calls_resolve_through_imports_not_bare_names() -> None:
    changed = file_symbols("checkout.py", "def total(x):\n    return x + 1\n", [(2, 2)])
    # Module-qualified call into checkout -> real dependency.
    via_module = file_symbols("t.py", "import checkout\n\ncheckout.total(3)\n", [(3, 3)])
    assert symbol_overlap(changed, via_module)[0] == ("total",)
    # Aliased from-import -> real dependency.
    via_alias = file_symbols("t.py", "from checkout import total as t\n\nt(3)\n", [(3, 3)])
    assert symbol_overlap(changed, via_alias)[0] == ("total",)
    # Same name from a different module -> not a dependency.
    other_module = file_symbols("t.py", "import invoice\n\ninvoice.total(3)\n", [(3, 3)])
    assert symbol_overlap(changed, other_module) == ((), ())
    # A method call on some object -> only matches a changed method.
    method_call = file_symbols("t.py", "def f(cart):\n    return cart.total()\n", [(2, 2)])
    assert symbol_overlap(changed, method_call) == ((), ())
    # A bare name that is neither imported nor defined here (builtin/local).
    bare = file_symbols("t.py", "def f():\n    return total(1)\n", [(2, 2)])
    assert symbol_overlap(changed, bare) == ((), ())


def test_changed_method_matches_method_calls() -> None:
    src = "class Cart:\n    def total(self):\n        return 1\n"
    changed = file_symbols("cart.py", src, [(3, 3)])
    assert {(d.name, d.kind) for d in changed.defs} == {("total", "method")}
    caller = file_symbols("t.py", "def f(cart):\n    return cart.total()\n", [(2, 2)])
    assert symbol_overlap(changed, caller)[0] == ("total",)


def test_class_field_change_counts_as_a_class_change() -> None:
    src = (
        "from pydantic import BaseModel\n\n\n"
        "class Preview(BaseModel):\n    a: int\n    b: int\n"
        "    def label(self):\n        return 'x'\n"
    )
    # A new required field on line 6 changes the constructor.
    assert ("Preview", "class") in {(d.name, d.kind) for d in file_symbols("m.py", src, [(6, 6)]).defs}
    # A method body change is a method change, not a constructor change.
    assert {(d.name, d.kind) for d in file_symbols("m.py", src, [(8, 8)]).defs} == {
        ("label", "method")
    }


def test_calls_follow_reexports(tmp_path: Path) -> None:
    r = tmp_path / "rx"
    (r / "pkg" / "core").mkdir(parents=True)
    (r / "pkg" / "ui").mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "pkg" / "core" / "models.py").write_text("class Preview:\n    a: int\n")
    (r / "pkg" / "ui" / "state.py").write_text("from pkg.core.models import Preview\n")
    (r / "pkg" / "ui" / "__init__.py").write_text("from .state import Preview\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    _git(r, "checkout", "-q", "-b", "fields")
    (r / "pkg" / "core" / "models.py").write_text("class Preview:\n    a: int\n    b: int\n")
    _git(r, "commit", "-qam", "new required field")
    _git(r, "checkout", "-q", "main")
    _git(r, "checkout", "-q", "-b", "caller")
    (r / "test_ui.py").write_text("from pkg.ui import Preview\n\n\ndef test_p():\n    Preview(a=1)\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "caller via two re-exports")
    _git(r, "checkout", "-q", "main")

    def syms(branch: str):
        diff = git_service.diff_against_base(r, branch, "main")
        assert isinstance(diff, BranchDiff)
        return branch_symbols(r, diff)

    names, evidence = symbol_overlap(syms("fields"), syms("caller"), "fields", "caller")
    assert names == ("Preview",)
    assert evidence == (
        "Preview: changed by fields (pkg/core/models.py), called by caller (test_ui.py)",
    )


def test_regex_heuristic_for_other_languages() -> None:
    js = "export function price(a) {\n  return a * 2;\n}\nconst total = (x) => price(x) + 1;\n"
    syms = regex_symbols(js, [(2, 2)])
    assert "price" in syms.def_names
    caller = regex_symbols(js, [(4, 4)])
    assert "price" in caller.call_names
    assert "total" in caller.def_names
    assert "if" not in regex_symbols("if (x) { go(1) }\n", [(1, 1)]).call_names
    assert symbol_overlap(syms, caller)[0] == ("price",)


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
    assert defcall.symbol_evidence == [
        "f: changed by feat-def (lib.py), called by feat-call (app.py)"
    ]
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


def test_signature_change_and_call_mismatch() -> None:
    import ast

    from app.services.prefilter import call_mismatch, signature_change

    old = ast.parse("class P:\n    a: int\n    b: int = 0\n").body[0]
    new = ast.parse("class P:\n    a: int\n    b: int = 0\n    c: int\n    d: int = 1\n").body[0]
    assert signature_change(old, new) == "breaking signature change: new required c"
    ok_call = ast.parse("P(a=1, c=2)").body[0].value
    bad_call = ast.parse("P(a=1)").body[0].value
    splat = ast.parse("P(**kw)").body[0].value
    assert call_mismatch(ok_call, old, new) is None
    assert call_mismatch(bad_call, old, new) == "call site does not pass new required c"
    assert call_mismatch(splat, old, new) is None  # unknown: not claimed
    f_old = ast.parse("def f(x, ctx=None):\n    pass\n").body[0]
    f_new = ast.parse("def f(x, *, flag=False):\n    pass\n").body[0]
    assert signature_change(f_old, f_new) == "breaking signature change: removed ctx"
    assert call_mismatch(ast.parse("f(x=1, ctx=2)").body[0].value, f_old, f_new) == (
        "call site still passes removed ctx"
    )
    assert call_mismatch(ast.parse("f(x=1)").body[0].value, f_old, f_new) is None
    assert signature_change(f_old, ast.parse("def f(x, ctx=None, y=1):\n    pass\n").body[0]) == (
        "compatible signature change: new optional y"
    )


def test_evidence_snippets_show_definition_diff_and_call_site(tmp_path: Path) -> None:
    from app.services.prefilter import evidence_snippets

    r = tmp_path / "ev"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "models.py").write_text("class Preview:\n    a: int\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    _git(r, "checkout", "-q", "-b", "fields")
    (r / "models.py").write_text("class Preview:\n    a: int\n    b: int\n")
    _git(r, "commit", "-qam", "required b")
    _git(r, "checkout", "-q", "main")
    _git(r, "checkout", "-q", "-b", "caller")
    (r / "use.py").write_text("from models import Preview\n\nP = Preview(a=1)\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "caller")
    text = evidence_snippets(
        r, "main", ["Preview: changed by fields (models.py), called by caller (use.py)"]
    )
    assert text.startswith(
        "[breaking signature change: new required b; call site does not pass new required b]"
    )
    assert "+    b: int" in text
    assert "    3 | P = Preview(a=1)" in text
