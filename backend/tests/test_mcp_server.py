"""The MCP server end to end over the SDK's in-memory transport, engine mocked."""
from __future__ import annotations

import os

import anyio
import pytest

pytest.importorskip("mcp")

from mcp.client import Client  # noqa: E402

from tests import test_mcp_tools  # noqa: E402

FakeEngine = test_mcp_tools.FakeEngine
repo = test_mcp_tools.repo  # fixture


@pytest.fixture()
def mcp_server(monkeypatch: pytest.MonkeyPatch):
    cwd = os.getcwd()
    from mergeguard_mcp import server

    os.chdir(cwd)  # importing the server chdirs to backend/; keep the test cwd
    engine = FakeEngine()
    monkeypatch.setattr("app.services.sentinel.analyze", engine)
    return server.server, engine


def test_lists_three_tools_and_calls_them(mcp_server, repo) -> None:
    srv, engine = mcp_server

    async def go():
        async with Client(srv) as client:
            listed = await client.list_tools()
            names = sorted(t.name for t in listed.tools)
            ok = await client.call_tool("check_merge_risk",
                                        {"repo_path": str(repo), "branch": "feat/tax"})
            bad = await client.call_tool("list_conflicting_branches", {"repo_path": "/etc"})
            order = await client.call_tool("suggest_merge_order",
                                           {"repo_path": str(repo), "verify": False})
            return names, ok, bad, order

    names, ok, bad, order = anyio.run(go)
    assert names == ["check_merge_risk", "list_conflicting_branches", "suggest_merge_order"]
    assert not ok.is_error
    assert ok.structured_content["pairs"][0]["verdict"] == "confirmed"
    assert bad.is_error and "outside the allowed roots" in bad.content[0].text
    assert "Traceback" not in bad.content[0].text
    assert [s["branch"] for s in order.structured_content["order"]][-1] == "feat/tax"
    assert engine.calls[-1]["verify"] is False


def test_env_file_supplies_only_nebius_settings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = os.getcwd()
    from mergeguard_mcp import server

    os.chdir(cwd)
    env_file = tmp_path / "secrets.env"
    env_file.write_text("NEBIUS_API_KEY=from-file-123\nOTHER_TOKEN=keep-out\n")
    monkeypatch.setenv("MERGEGUARD_ENV_FILE", str(env_file))
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.delenv("OTHER_TOKEN", raising=False)
    server._load_env_file()
    assert os.environ.get("NEBIUS_API_KEY") == "from-file-123"
    assert "OTHER_TOKEN" not in os.environ
    # An explicitly set environment value wins over the file.
    monkeypatch.setenv("NEBIUS_API_KEY", "explicit")
    server._load_env_file()
    assert os.environ["NEBIUS_API_KEY"] == "explicit"
