#!/usr/bin/env python3
"""End-to-end check of the MergeGuard MCP server over real stdio.

Spawns ``mergeguard_mcp/server.py`` with the given interpreter (the MCP venv),
connects with the official MCP Python client, lists the tools and calls each
one on the bundled checkout demo and the daimon real case (demo/real-case-1).
The first call per repo is fresh (use_cache=False) so its cost is real; the
rest reuse the engine's caches. Full JSON results go to
backend/.workspace/mcp-e2e/<timestamp>/; a trimmed view is printed.

    NEBIUS_API_KEY=... python scripts/mcp_e2e.py   # run with the MCP venv python
    python scripts/mcp_e2e.py --cached             # every call may reuse the caches
    python scripts/mcp_e2e.py --env-file ~/x.env   # key via MERGEGUARD_ENV_FILE, as
                                                   # in the client configs

The key is passed to the server process from this process's environment
only; it is never printed or written.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import anyio
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

BACKEND = Path(__file__).resolve().parents[1]
DEMO = BACKEND.parent / "demo"
CALLS = [
    ("checkout", "check_merge_risk",
     {"repo_path": str(DEMO / "checkout-demo"), "branch": "feat/tax-before-discount",
      "use_cache": False}),
    ("checkout", "list_conflicting_branches", {"repo_path": str(DEMO / "checkout-demo")}),
    ("checkout", "suggest_merge_order", {"repo_path": str(DEMO / "checkout-demo")}),
    ("real-case-1", "check_merge_risk",
     {"repo_path": str(DEMO / "real-case-1"), "branch": "pr-17-deploy-review-findings",
      "use_cache": False}),
    ("real-case-1", "list_conflicting_branches", {"repo_path": str(DEMO / "real-case-1")}),
    ("real-case-1", "suggest_merge_order", {"repo_path": str(DEMO / "real-case-1")}),
    ("guardrail", "list_conflicting_branches", {"repo_path": "/etc"}),
    ("guardrail", "check_merge_risk",
     {"repo_path": str(DEMO / "checkout-demo"), "branch": "no-such-branch"}),
]


async def main() -> int:
    out_dir = BACKEND / ".workspace" / "mcp-e2e" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    argv = sys.argv[1:]
    if "--env-file" in argv:  # exactly what the Cursor / Claude Code configs pass
        env = {"MERGEGUARD_ENV_FILE": argv[argv.index("--env-file") + 1]}
    else:
        env = {k: os.environ[k] for k in ("NEBIUS_API_KEY", "VERIFY_ALLOWED_ROOTS")
               if os.environ.get(k)}
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(BACKEND / "mergeguard_mcp" / "server.py")],
                                   env=env, cwd="/")
    spent = 0.0
    cached = "--cached" in sys.argv[1:]
    async with Client(params) as client:
        tools = await client.list_tools()
        print("tools:", ", ".join(sorted(t.name for t in tools.tools)), flush=True)
        for i, (label, name, args) in enumerate(CALLS, 1):
            if cached:
                args = {k: v for k, v in args.items() if k != "use_cache"}
            started = time.monotonic()
            progress: list[str] = []

            async def on_progress(value, total, message, _seen=progress):
                _seen.append(message or "")

            res = await client.call_tool(name, args, read_timeout_seconds=900,
                                         progress_callback=on_progress)
            elapsed = time.monotonic() - started
            record = {"label": label, "tool": name, "arguments": args, "elapsed_s": round(elapsed, 1),
                      "is_error": res.is_error, "progress": progress,
                      "structured": res.structured_content,
                      "text": [getattr(c, "text", None) for c in res.content]}
            (out_dir / f"{i:02d}-{label}-{name}.json").write_text(json.dumps(record, indent=2))
            cost = (res.structured_content or {}).get("cost", {})
            spent += float(cost.get("estimated_cost_usd") or 0.0)
            head = (res.content[0].text[:160] if res.is_error else
                    (res.structured_content or {}).get("summary") or "")
            note = (res.structured_content or {}).get("note")
            print(f"[{i}] {label} {name} {elapsed:.1f}s error={res.is_error} "
                  f"${cost.get('estimated_cost_usd', 0)} progress={len(progress)}"
                  f"{' NOTE: ' + note if note else ''} | {head}", flush=True)
    print(f"total estimated spend: ${spent:.4f}\nresults: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(anyio.run(main))
