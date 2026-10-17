"""Exercise real Git merges; mock only external inference/research services."""
from __future__ import annotations

import json
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import ResolveRequest
from app.services import git_service as git, resolver


def run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True).stdout.decode().strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.name", "Test")
    run(repo, "config", "user.email", "test@example.com")
    run(repo, "config", "core.autocrlf", "false")
    (repo / "app.py").write_bytes(b"value = 0\n")
    run(repo, "add", ".")
    run(repo, "commit", "-m", "base")
    run(repo, "switch", "-c", "target")
    (repo / "app.py").write_bytes(b"value = 1\n")
    run(repo, "commit", "-am", "target change")
    run(repo, "switch", "-c", "source", "main")
    (repo / "app.py").write_bytes(b"value = 2\n")
    (repo / "source_only.txt").write_bytes(b"clean source change\n")
    run(repo, "add", ".")
    run(repo, "commit", "-m", "source change")
    run(repo, "switch", "target")
    return repo


def request(repo, **kwargs):
    return ResolveRequest(repo_path=str(repo), target_branch="target", source_branch="source", **kwargs)


@pytest.fixture
def model(monkeypatch):
    def propose(context, documentation):
        assert context["base"] == "value = 0\n"
        assert context["target"] == "value = 1\n"
        assert context["source"] == "value = 2\n"
        return resolver.Proposal(needs_review=False, content="value = 1 + 2\n",
                                 explanation="Combine both contributions")
    monkeypatch.setattr(resolver, "propose", propose)


def test_proposal_is_complete_applicable_and_preserves_dirty_checkout(repo, model, tmp_path):
    (repo / "app.py").write_text("uncommitted work\n")
    (repo / "untracked.txt").write_text("keep me")
    before = run(repo, "status", "--porcelain")
    worktrees = run(repo, "worktree", "list", "--porcelain")
    refs = run(repo, "show-ref")
    result = resolver.resolve(request(repo))
    assert result.status == "proposed"
    assert result.files[0].status == "resolved"
    assert "source_only.txt" in result.patch
    assert result.validations[0].status == "passed"
    assert result.validations[1].status == "skipped"
    assert run(repo, "status", "--porcelain") == before
    assert run(repo, "worktree", "list", "--porcelain") == worktrees
    assert run(repo, "show-ref") == refs
    assert (repo / "app.py").read_text() == "uncommitted work\n"
    patch = tmp_path / "resolution.patch"
    patch.write_bytes(result.patch.encode("utf-8"))
    with git.merge_worktree(repo, result.target_commit) as checkout:
        run(checkout, "apply", "--check", str(patch))
        run(checkout, "apply", str(patch))
        assert (checkout / "app.py").read_text() == "value = 1 + 2\n"
        assert (checkout / "source_only.txt").read_text() == "clean source change\n"


def test_clean_merge_needs_no_model(repo, monkeypatch):
    monkeypatch.setattr(resolver, "propose", lambda *args: pytest.fail("Unexpected model call"))
    req = request(repo)
    req.target_branch = "main"
    result = resolver.resolve(req)
    assert result.status == "clean"
    assert result.files == []
    assert "source_only.txt" in result.patch


@pytest.mark.parametrize("content,review,status", [
    ("<<<<<<< ours\nvalue = 1\n=======\nvalue = 2\n>>>>>>> theirs\n", False, "needs_review"),
    ("value = (\n", False, "validation_failed"),
    ("", True, "needs_review"),
])
def test_bad_or_ambiguous_proposals(repo, monkeypatch, content, review, status):
    monkeypatch.setattr(resolver, "propose", lambda *args: resolver.Proposal(
        needs_review=review, content=content, explanation="Human decision needed"))
    result = resolver.resolve(request(repo))
    assert result.status == status
    if status == "needs_review":
        assert result.patch is None
    assert len(run(repo, "worktree", "list", "--porcelain").split("worktree ")) == 2


def test_unconfigured_nebius(repo, monkeypatch):
    monkeypatch.setattr(settings, "nebius_api_key", "")
    result = resolver.resolve(request(repo))
    assert result.status == "needs_review"
    assert "NEBIUS_API_KEY" in result.files[0].explanation
    assert result.patch is None


def test_delete_modify_requires_review(repo, monkeypatch):
    run(repo, "switch", "source")
    run(repo, "rm", "app.py")
    run(repo, "commit", "-m", "delete")
    run(repo, "switch", "target")
    monkeypatch.setattr(resolver, "propose", lambda *args: pytest.fail("Unsupported conflict"))
    result = resolver.resolve(request(repo))
    assert result.status == "needs_review"
    assert "delete/modify" in result.files[0].explanation


def test_binary_conflict_requires_review(repo, monkeypatch):
    for branch, content in [("target", b"ours\0"), ("source", b"theirs\0")]:
        run(repo, "switch", branch)
        (repo / "app.py").write_bytes(content)
        run(repo, "commit", "-am", "binary")
    monkeypatch.setattr(resolver, "propose", lambda *args: pytest.fail("Binary conflict"))
    result = resolver.resolve(request(repo))
    assert result.status == "needs_review"
    assert "Binary" in result.files[0].explanation


def test_context_limit(repo, monkeypatch):
    monkeypatch.setattr(settings, "resolver_max_file_bytes", 2)
    monkeypatch.setattr(resolver, "propose", lambda *args: pytest.fail("Oversized context"))
    assert resolver.resolve(request(repo)).status == "needs_review"


def test_research_failure_is_reported(repo, model, monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "")
    result = resolver.resolve(request(repo, research_query="Python documentation"))
    assert result.status == "proposed"
    assert result.warnings and result.research_sources == []


@pytest.mark.parametrize("exit_code,status", [(0, "proposed"), (1, "validation_failed")])
def test_configured_tests(repo, model, monkeypatch, exit_code, status):
    monkeypatch.setattr(settings, "resolver_test_command", [sys.executable, "-c",
        f"from pathlib import Path; assert Path('app.py').read_text() == 'value = 1 + 2\\n'; "
        f"raise SystemExit({exit_code})"])
    result = resolver.resolve(request(repo, run_tests=True))
    assert result.status == status
    assert result.validations[-1].status == ("passed" if exit_code == 0 else "failed")


def test_test_command_timeout(repo, model, monkeypatch):
    monkeypatch.setattr(settings, "resolver_test_command", [sys.executable, "-c", "import time; time.sleep(5)"])
    monkeypatch.setattr(settings, "resolver_test_timeout", 1)
    result = resolver.resolve(request(repo, run_tests=True))
    assert result.status == "validation_failed"
    assert "timed out" in result.validations[-1].output


def test_api_and_invalid_ref(repo, model):
    client = TestClient(app)
    response = client.post("/api/resolve", json=request(repo).model_dump())
    assert response.status_code == 200
    assert response.json()["status"] == "proposed"
    payload = request(repo).model_dump()
    payload["source_branch"] = "--help"
    assert client.post("/api/resolve", json=payload).status_code == 400
    assert client.post("/api/resolve", json={}).status_code == 422
    # The existing Sentinel endpoint must still return an analysis response.
    response = client.post("/api/analyze", json={"repo_path": str(repo), "base_branch": "main"})
    assert response.status_code == 200
    assert response.json()["pairs"]


@pytest.mark.parametrize("body", [
    {"choices": [{"finish_reason": "stop", "message": {"content": "invalid json"}}]},
    {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
    {"choices": []},
])
def test_invalid_provider_payloads(monkeypatch, body):
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    original_client = httpx.Client
    monkeypatch.setattr(resolver.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    with pytest.raises(resolver.ProviderError):
        resolver.propose({}, "")


def test_nebius_and_tavily_protocol(monkeypatch):
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr(settings, "tavily_api_key", "research-key")
    def handler(req):
        payload = json.loads(req.content)
        if req.url.host == "api.tavily.com":
            assert payload["query"] == "library docs"
            return httpx.Response(200, json={"results": [{"url": "https://example.com/docs", "content": "Docs"}]})
        assert req.headers["Authorization"] == "Bearer test-key"
        assert payload["model"] == settings.nebius_model
        assert json.loads(payload["messages"][1]["content"])["documentation"]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"needs_review": False, "content": "value = 3\n", "explanation": "Combined"})}}]})
    original_client = httpx.Client
    monkeypatch.setattr(resolver.httpx, "Client", lambda **kwargs:
        original_client(transport=httpx.MockTransport(handler), **kwargs))
    docs, sources = resolver.research("library docs")
    assert sources == ["https://example.com/docs"]
    assert resolver.propose({}, docs).content == "value = 3\n"


def test_cleanup_after_model_crash(repo, monkeypatch):
    before = run(repo, "worktree", "list", "--porcelain")
    def crash(*args):
        raise RuntimeError("unexpected failure")
    monkeypatch.setattr(resolver, "propose", crash)
    with pytest.raises(RuntimeError):
        resolver.resolve(request(repo))
    assert run(repo, "worktree", "list", "--porcelain") == before


def test_add_add_and_filename_with_spaces(repo, monkeypatch):
    for branch, content in [("target", b"target\n"), ("source", b"source\n")]:
        run(repo, "switch", branch)
        (repo / "new file.txt").write_bytes(content)
        run(repo, "add", ".")
        run(repo, "commit", "-m", "add new file")
    seen = []
    def propose(context, documentation):
        seen.append(context)
        return resolver.Proposal(needs_review=False, content="combined\n", explanation="Both")
    monkeypatch.setattr(resolver, "propose", propose)
    result = resolver.resolve(request(repo))
    assert result.status == "proposed"
    assert next(c for c in seen if c["path"] == "new file.txt")["base"] is None


def test_no_configured_test_command(repo, model, monkeypatch):
    monkeypatch.setattr(settings, "resolver_test_command", [])
    result = resolver.resolve(request(repo, run_tests=True))
    assert result.validations[-1].status == "skipped"
    assert "not configured" in result.validations[-1].output


def test_hooks_are_disabled(repo, model):
    # Git for Windows runs executable shell hooks; failing hooks would prevent checkout.
    for name in ["post-checkout", "pre-merge-commit"]:
        hook = repo / ".git" / "hooks" / name
        hook.write_bytes(b"#!/bin/sh\nexit 42\n")
        hook.chmod(0o755)
    assert resolver.resolve(request(repo)).status == "proposed"


def test_non_utf8_requires_review(repo, monkeypatch):
    for branch, content in [("target", b"ours\xff\n"), ("source", b"theirs\xff\n")]:
        run(repo, "switch", branch)
        (repo / "app.py").write_bytes(content)
        run(repo, "commit", "-am", "non UTF-8")
    monkeypatch.setattr(resolver, "propose", lambda *args: pytest.fail("Non-UTF-8 conflict"))
    result = resolver.resolve(request(repo))
    assert result.status == "needs_review"
    assert "Non-UTF-8" in result.files[0].explanation


def test_non_utf8_clean_diff_does_not_return_corrupt_patch(repo):
    run(repo, "switch", "source")
    (repo / "source_only.txt").write_bytes(b"non-UTF-8\xff\n")
    run(repo, "commit", "-am", "non-UTF-8 clean change")
    req = request(repo)
    req.target_branch = "main"
    result = resolver.resolve(req)
    assert result.status == "needs_review"
    assert result.patch is None
    assert "non-UTF-8" in result.warnings[0]


def test_extended_conflict_markers_are_rejected(repo, monkeypatch):
    monkeypatch.setattr(resolver, "propose", lambda *args: resolver.Proposal(
        needs_review=False, content="<<<<<<<< ours\nx\n========\ny\n>>>>>>>> theirs\n",
        explanation="Incomplete"))
    assert resolver.resolve(request(repo)).status == "needs_review"
