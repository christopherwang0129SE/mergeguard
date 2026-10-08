"""MergeGuard MCP server (stdio).

    python -m mergeguard_mcp            # from backend/
    python /abs/path/backend/mergeguard_mcp/server.py

Environment:
- NEBIUS_API_KEY: enables Nemotron scoring and verify-by-test (read from the
  environment only; never logged or returned).
- MERGEGUARD_ENV_FILE: optional dotenv file to read NEBIUS_* settings from
  (only NEBIUS_* keys are taken), so client configs never hold the key.
- VERIFY_ALLOWED_ROOTS: extra os.pathsep-separated folders whose repos the
  tools may analyse (the bundled demo/ folder is always allowed).
"""
from __future__ import annotations

import os
import sys
import time
from functools import partial
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _load_env_file() -> None:
    path = os.environ.get("MERGEGUARD_ENV_FILE", "").strip()
    if not path:
        return
    from dotenv import dotenv_values

    for key, value in dotenv_values(Path(path).expanduser()).items():
        if key.startswith("NEBIUS_") and value and key not in os.environ:
            os.environ[key] = value


# Engine paths (cache, verify artefacts) are relative to backend/.
os.chdir(BACKEND)
_load_env_file()

import anyio  # noqa: E402
from mcp.server.mcpserver import Context, MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from mergeguard_mcp import tools  # noqa: E402

server = MCPServer(
    name="mergeguard",
    instructions=(
        "MergeGuard predicts semantic merge conflicts between git branches of a local "
        "repository. Verdicts: 'confirmed' means a generated test passes on each branch "
        "and fails on their merge; 'suspected' and 'not verified' are risk estimates. "
        "Calls may take a minute and spend a few cents of model tokens (see 'cost')."
    ),
)


# A fresh analysis takes 20-80 s (model scoring plus generated tests). While it
# runs, send a progress notification every few seconds so clients that track
# progress (and reset their request timeout on it) show it is alive. A no-op
# for clients that did not ask for progress.
HEARTBEAT_S = 5.0


async def _with_progress(ctx: Context, label: str, fn, *args, **kwargs) -> dict[str, Any]:
    started = time.monotonic()
    outcome: dict[str, Any] = {}

    async def notify(step: float, message: str) -> None:
        try:
            await ctx.report_progress(step, None, message)
        except Exception:  # noqa: BLE001 - progress is best effort
            pass

    async def work(scope: anyio.CancelScope) -> None:
        try:
            outcome["value"] = await anyio.to_thread.run_sync(partial(_call, fn, *args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            outcome["error"] = exc
        scope.cancel()

    async with anyio.create_task_group() as tg:
        tg.start_soon(work, tg.cancel_scope)
        await notify(0, f"{label}: started")
        step = 0
        while True:
            await anyio.sleep(HEARTBEAT_S)
            step += 1
            await notify(step, f"{label}: still analysing ({time.monotonic() - started:.0f} s)")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except tools.ToolInputError as exc:
        raise ToolError(tools.scrub(str(exc))) from None
    except Exception as exc:  # noqa: BLE001 - report a clean message, no traceback
        raise ToolError(tools.scrub(f"MergeGuard failed: {type(exc).__name__}: {exc}")[:500]) from None


@server.tool(structured_output=True)
async def check_merge_risk(repo_path: str, branch: str, base: str = "main", verify: bool = True,
                           use_cache: bool = True, ctx: Context = None) -> dict[str, Any]:
    """Risk of merging `branch` against every other local branch of the repo.

    Returns pairs ranked most dangerous first, each with level, score, verdict
    (confirmed / suspected / error / not verified), rationale, symbol evidence
    and token use, plus the total estimated cost. `repo_path` must be an
    absolute path under the allowed roots. `verify=False` skips test
    generation (cheaper, no 'confirmed' verdicts).
    """
    return await _with_progress(ctx, "check_merge_risk", tools.check_merge_risk, repo_path,
                                branch, base, verify=verify, use_cache=use_cache)


@server.tool(structured_output=True)
async def list_conflicting_branches(repo_path: str, base: str = "main", verify: bool = True,
                                    use_cache: bool = True,
                                    ctx: Context = None) -> dict[str, Any]:
    """All risky branch pairs of the repo (confirmed, suspected, medium/high
    risk, static call-site breaks or textual conflicts), most dangerous first."""
    return await _with_progress(ctx, "list_conflicting_branches",
                                tools.list_conflicting_branches, repo_path, base, verify=verify,
                                use_cache=use_cache)


@server.tool(structured_output=True)
async def suggest_merge_order(repo_path: str, base: str = "main", verify: bool = True,
                              use_cache: bool = True, ctx: Context = None) -> dict[str, Any]:
    """A deterministic merge order with the reason for every step: branches
    with no risky pair first, unconfirmed risky ones next, test-confirmed
    conflicts last and one at a time (the partner is rebased and fixed)."""
    return await _with_progress(ctx, "suggest_merge_order", tools.suggest_merge_order,
                                repo_path, base, verify=verify, use_cache=use_cache)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
