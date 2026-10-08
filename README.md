# MergeGuard

**An agent-based system that minimises merge conflicts.**
Built for the **Nebius & Tavily Hackathon 2026**.

Naive parallel development — whether by humans or a fleet of AI coding agents —
collides. MergeGuard watches in-flight branches and works to **predict**, **prevent**,
and **resolve** merge conflicts before they cost anyone a day.

## The three agents

| Agent | Role | Status |
|-------|------|--------|
| **Sentinel** | *Predict* — scores conflict risk between every pair of in-flight branches from git hunk overlap, then optionally refines the riskiest pairs with NVIDIA Nemotron on Nebius Token Factory. | ✅ MVP |
| **Planner** | *Prevent* — decomposes work into non-overlapping "zones" and suggests merge/rebase order. | ⏳ planned |
| **Merger** | *Resolve* — semantic 3-way merge; uses **Tavily** to check current library docs, runs tests, explains the resolution. | ⏳ planned |

- **LLM inference:** Nebius Token Factory (OpenAI-compatible API)
- **Web research:** Tavily
- **Backend:** FastAPI (Python) · **Frontend:** dashboard (branch graph + risk heatmap)

## Quickstart (backend)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env    # fill in keys (optional for Sentinel)
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the API.

### Try the Sentinel on any repo

```bash
curl -s -X POST http://localhost:8000/api/analyze \
  -H 'content-type: application/json' \
  -d '{"repo_path": "/path/to/some/repo", "base_branch": "main"}' | python3 -m json.tool
```

Returns a per-branch diff summary and a ranked list of branch-pair **conflict-risk
scores** with human-readable rationales.

### Nemotron risk refinement

With no API key, Sentinel is unchanged: each pair's `score`, `level`, and
`rationale` come from git hunk overlap (including a 3-line adjacency window).
The response adds `score_source: "git"` and `fallback_reason: "no_api_key"`.

To ask NVIDIA Nemotron 3 Super as well, copy `.env.example` to `backend/.env`
and set:

| Variable | Role |
|----------|------|
| `NEBIUS_API_KEY` | Token Factory key. Never commit it. An empty key disables the model. |
| `NEBIUS_BASE_URL` | Default `https://api.tokenfactory.nebius.com/v1/` (Nebius Token Factory, OpenAI-compatible). |
| `NEBIUS_MODEL` | Default `nvidia/nemotron-3-super-120b-a12b`. |
| `NEBIUS_ENABLED` | `false` forces the git scorer even when a key is set. |
| `NEBIUS_TIMEOUT_SECONDS` | One chat-completions attempt. The client does not retry. Default 40. |
| `NEBIUS_MAX_PAIRS` | Top-K: cap on model calls per `/api/analyze` after the static prefilter. Highest signal first (overlapping files, symbol overlap, shared files, git score). Default 5. |
| `NEMOTRON_INPUT_USD_PER_1M` / `NEMOTRON_OUTPUT_USD_PER_1M` | Listed Nemotron 3 Super price used for the `$` estimate. Defaults 0.30 / 0.90. |

For each selected pair the model receives a bounded prompt: both branches'
changed files, overlapping or adjacent hunks, a trimmed diff excerpt, and a
few recent commit subjects. It must reply with JSON
`{"risk_score", "risk_level", "rationale"}`.

The published `score` is `0.5 * git_score + 0.5 * risk_score`. If the hunks
overlap, that blend cannot drop below the git score. `level` follows the
published score (high ≥ 70, medium ≥ 40, low > 0). `score_source` is `"model"`,
`rationale` is the model's rationale, and `git_score` / `git_rationale` keep
the heuristic. `risk_model` on the response is the model id when any pair used
it.

If the call errors, times out, or returns something that is not that JSON, the
pair keeps the git score, `score_source` stays `"git"`, and `fallback_reason`
is `"model_error"` or `"unparsable"`. Pairs past the cap use `"pair_cap"`.
`NEBIUS_ENABLED=false` uses `"disabled"`. The HTTP request still succeeds.

### Cost-aware pipeline

1. **Static prefilter (no LLM).** For every branch, Sentinel extracts the
   functions/classes/constants it changed and the calls made from changed
   code. Python uses `ast` and resolves calls through the file's imports:
   `checkout.total()` only matches a `total` defined in `checkout.py`, a bare
   `total()` only matches one imported from or defined in its own module, and
   `obj.total()` only matches a changed *method*. Other languages use a
   `def`/`function`/`func`/`fn` regex heuristic matched by name. An overlap
   needs one branch to have CHANGED the definition (both changed it, or one
   changed it and the other calls it); calls both branches make to a function
   neither changed never count. A pair is a model candidate only if the
   branches share a file or have such an overlap; other pairs get
   `fallback_reason: "prefiltered"` and never reach the model. Each pair
   carries `symbol_overlap` and `symbol_evidence` (who changed it, who calls
   it, in which file).
2. **Top-K.** Only the `NEBIUS_MAX_PAIRS` (default 5) strongest candidates
   are sent to Nemotron; the rest get `"pair_cap"`.
3. **Disk cache.** Model results are cached under
   `WORKSPACE_DIR/nemotron-cache/`, keyed by (base sha, head A sha, head B sha,
   model, prompt version). An identical re-run makes **0 LLM calls**. Send
   `"use_cache": false` in the request to force fresh calls.
4. **Accounting.** The response has `usage`: `llm_calls`, `cache_hits`,
   `prompt_tokens`, `completion_tokens`, `total_tokens`,
   `estimated_cost_usd`, prefilter/top-K counts, and `elapsed_s`. The dashboard
   summary bar shows calls, tokens, and estimated $.

### Verify-by-test (semantic conflicts, with evidence)

For pairs scored **high risk** (model level `high`, or published/model score
≥ `VERIFY_MIN_SCORE`, default 70), Sentinel asks Nemotron to write one small
pytest module: `test_branch_a_*` tests capture branch A's intent and
`test_branch_b_*` tests capture branch B's intent. It then runs that module in
throwaway `git worktree`s:

| Checkout | Tests run | Expectation |
|---|---|---|
| branch A | `-k branch_a` | pass |
| branch B | `-k branch_b` | pass |
| merge-base of A and B | all | shows which tests capture a real change |
| A merged with B (`git merge --no-commit`) | all | an intent test fails |

`verification.status` is:

- `confirmed`: A's tests pass on A, B's pass on B, and at least one intent test
  that **fails on the merge-base** (so it captures its branch's change) **fails
  on the merge**. A test that only pins unchanged base behaviour can never
  confirm, so a docs-only branch cannot be "confirmed" against a code change.
  A confirmed verdict is authoritative: the pair's `level` becomes `high`
  and its shown `score` is floored at `VERIFY_CONFIRMED_FLOOR` (default 90).
  `git_score` and `model_score` are left as-is, the pre-verify blend is kept
  in `blended_score`, and `score_note` is `"raised: confirmed by test"`.
- `suspected`: the test ran but did not meet that bar, for example when it
  also passes on the merge; the blended score is kept. `error`: no usable test could be generated or run.
  `skipped`: not eligible (for example not a local repo under the allowed roots).

If a test fails on its own branch, or does not exercise the interaction,
Nemotron gets a repair round with the real pytest output
(`VERIFY_MAX_ATTEMPTS`, default 3 generations in total). If Nemotron's
reasoning runs out of its token budget, the next attempt is made with
reasoning turned off. The generated test, per-checkout pass/fail
counts and per-test outcomes, trimmed pytest output, attempts, tokens and the
artifact path (`WORKSPACE_DIR/verify/<key>/`) are returned under
`pairs[].verification`. The test text is cached with the same SHA-based key, so
an identical re-run makes no LLM calls (the test is re-executed).

Safety:

- Only local repos under `VERIFY_ALLOWED_ROOTS` (default: this repo's `demo/`
  folder). Requests with `repo_url` never execute generated code.
- The subprocess gets a minimal environment (no API keys or other secrets),
  `VERIFY_TIMEOUT_SECONDS` (default 30) per run with the whole process group
  killed on timeout, and no network: macOS `sandbox-exec` (deny network, writes
  only in the temp dir) when available, plus an in-process socket guard.
- Generated code importing network/process modules or calling `open`, `eval`,
  `exec`, `os.system` and similar is rejected before it runs.
- Send `"verify": false` to skip it for a request.

## Real-world case

A real semantic merge conflict from a public repo, replayed end to end:
[pymc-labs/daimon](https://github.com/pymc-labs/daimon) (MIT, Python).

| | |
| --- | --- |
| Branch A | [PR #14](https://github.com/pymc-labs/daimon/pull/14) `feat/sync-fixes`, head [`1c4f6104`](https://github.com/pymc-labs/daimon/commit/1c4f61047aaff750c70b358209a3196715f42c0f). Adds two **required** fields, `slack_user_tokens` and `slack_turn_contexts`, to the pydantic model `PurgePreview` (`packages/core/daimon/core/privacy.py`) and updates every fixture that existed on its base. |
| Branch B | [PR #17](https://github.com/pymc-labs/daimon/pull/17) `fix/deploy-review-findings`, head [`7175c4d3`](https://github.com/pymc-labs/daimon/commit/7175c4d315ef8033d15afc8206249a1bd18991c4). Adds `packages/adapters/discord/tests/test_branding.py`, whose `_make_preview()` builds a `PurgePreview` with the old field set (imported through a re-export in `privacy_panel/state.py`). |
| Base | merge-base [`1933b244`](https://github.com/pymc-labs/daimon/commit/1933b2444b2d6e8ec1ee739d0d0109571d27406e) |
| Upstream | Both heads were green in CI (incl. `pytest-discord`). #17 merged 2026-07-27 01:35, #14 merged 03:07 (Copenhagen time). The merge commit [`dcec264b`](https://github.com/pymc-labs/daimon/commit/dcec264bdb0d5dbd57fc169d1033ed86b97c512c) failed [`pytest-discord`](https://github.com/pymc-labs/daimon/actions/runs/30229050079/job/89864379877) until [`239fdb55`](https://github.com/pymc-labs/daimon/commit/239fdb55ac47a9e655291b896f25807d7efd48be) (03:09) added the two fields to the fixture: *"Semantic merge conflict ... neither branch's CI saw the other."* |

Replay it locally (clones at pinned SHAs into `demo/real-case-1/`, gitignored;
needs `uv` and network once for the project venv; no Postgres needed):

```bash
python3 demo/replay_case_1.py --prove
```

What `--prove` shows (`pytest packages/adapters/discord/tests -k "branding or privacy_panel or purge"`):

| Checkout | Result |
| --- | --- |
| merge-base `1933b244` | 71 passed |
| A (`pr-14-sync-fixes`) | 71 passed |
| B (`pr-17-deploy-review-findings`) | 90 passed |
| `git merge --no-commit` B into A | "Automatic merge went well" (0 files changed by both PRs) |
| A+B | **2 failed**, 88 passed (`test_branding.py`: pydantic `ValidationError`, `slack_user_tokens` / `slack_turn_contexts` *Field required*) |
| A+B + upstream fix `239fdb55` | 90 passed |

What MergeGuard reported on `demo/real-case-1` (one fresh run, `use_cache=false`):

- **Prefilter**: candidate (0 shared files). Top evidence: `PurgePreview: changed by pr-14-sync-fixes (packages/core/daimon/core/privacy.py), called by pr-17-deploy-review-findings (packages/adapters/discord/tests/test_branding.py)`, labelled *breaking signature change: new required slack_user_tokens, slack_turn_contexts; call site does not pass new required ...*.
- **Nemotron**: 85/100, high (git heuristic: 0). It also claims that a `create_session` change breaks `bot.py`; the static evidence shows that call site does not pass the removed parameter, so that part of the rationale is wrong.
- **Verify-by-test**: **confirmed** on the first attempt. `test_branch_b_make_preview` (calls B's `_make_preview()`) passes on B, fails on the merge-base (module absent) and fails on A+B with the same `ValidationError` as upstream CI. Shown as **90 High** (raised: confirmed by test; blend 42.5).
- **Cost**: 2 LLM calls, 21,692 tokens, $0.0095 est., 53.8 s.

Nemotron output is not fully deterministic: earlier development runs on the same
case scored 85-88 and twice produced a test that did not pass on its own branch
(verdict *suspected*). Getting here took three general engine fixes, each with
tests: the prefilter now counts a changed class field as a constructor change
and follows re-exports (before, it found only `create_session`, `on_message`,
`on_submit`, `purge_account` and missed `PurgePreview`); the risk prompt now
fits the diff excerpt into the budget instead of dropping it whole and leads
with the changed definition and the other branch's call site (before: 45-48,
medium, not verified); and verify-by-test runs the repo's own `.venv` with
editable installs redirected to each worktree.

## Reliability eval

The model is nondeterministic, so one green run proves little.
`make eval-reliability` (needs `NEBIUS_API_KEY` in the environment) runs every
case N times from scratch (no model or test cache) and prints a table; raw
results and per-run timings land in `backend/.workspace/eval/<timestamp>/`
(rebuild the table later with `scripts/eval_reliability.py --summarize DIR`).

```bash
make eval-reliability N=5 FORCED=2 MAX_USD=0.30 PYTHON=../.venv-backend/bin/python
```

Cases: the real-world daimon pair above, the bundled checkout demo, and a
harmless fixture (`demo/make_harmless_repo.py`: an optional parameter, a new
caller, a behaviour-preserving refactor, a changelog) that must never be
confirmed. Because harmless pairs normally score too low to reach
verify-by-test, a stress case also forces verify-by-test on every harmless pair.

Measured on 2026-10-08 (`nvidia/nemotron-3-super-120b-a12b`, one run of the
command above):

| case | runs | pairs | confirmed | suspected | not verified | median time (s) | tokens | $ |
|---|---|---|---|---|---|---|---|---|
| real case (daimon #14 x #17) | 5 | 5 | 5 | 0 | 0 | 64.3 | 183,127 | 0.0862 |
| checkout demo, conflicting pair | 5 | 5 | 5 | 0 | 0 | 26.4 | 62,545 | 0.0354 |
| checkout demo, docs pairs | 5 | 10 | 0 | 0 | 10 | (same runs) | | |
| harmless fixture | 5 | 30 | 0 | 0 | 30 | 8.6 | 18,445 | 0.0082 |
| harmless fixture, verify forced on every pair | 2 | 12 | 0 | 12 | 0 | 303.9 | 180,940 | 0.1078 |

No errors and no rationale claims contradicted by the static checks in any run.

What makes it stable:

- **Exact static signature checks.** For every definition one branch changed
  and the other branch calls, every call site is checked against the new
  signature (required parameters, positional order, dataclass/pydantic fields).
  A proven break always goes to verify-by-test, whatever the model score.
- **Rationale claims are checked.** A rationale that says a call breaks where
  the static check proved every call site compatible gets one repair round;
  claims that survive are listed as unsupported in the UI.
- **Verify-by-test feedback.** The test prompt names the broken call site and
  its enclosing function; repair rounds name tests that only pin base
  behaviour. One confirming test is enough even if another generated test is broken.
- **Behaviour only.** A generated test that inspects code (source text, code
  objects, `hasattr`) or passes only while the other branch's change is
  absent never confirms. Failures are judged by the line they happen on, so a
  behavioural failure in the same test still counts. Before this rule, forcing
  verify on the harmless fixture produced 2 false confirmations in one run
  (tests asserting `'half' not in perimeter.__code__.co_varnames`).

## MCP server (for Cursor, Claude Code and other agents)

`backend/mergeguard_mcp/` is a stdio MCP server (official `mcp` Python SDK,
2.x) that runs the engine in-process, so an agent can ask "is it safe to merge
this branch?" before it merges. Tools:

| tool | what it returns |
|---|---|
| `check_merge_risk(repo_path, branch, base="main")` | every pair of `branch` with the other local branches, most dangerous first: level, score, verdict (`confirmed` / `suspected` / `error` / `not verified`), rationale, symbol evidence, static call-site breaks, tokens, plus total cost |
| `list_conflicting_branches(repo_path, base="main")` | all risky pairs (confirmed or suspected, medium/high risk, static breaks, textual conflicts) |
| `suggest_merge_order(repo_path, base="main")` | a deterministic order with a reason per step: no-risk branches first, unconfirmed risky ones next, test-confirmed conflicts last and one at a time (the partner is rebased and fixed before it merges) |

All three also take `verify` (default `true`; `false` skips test generation)
and `use_cache` (default `true`).

Guardrails: only absolute paths of local git repos under the allowed roots
(the bundled `demo/` folder plus `VERIFY_ALLOWED_ROOTS`, the same rule as
verify-by-test; symlinks are resolved first); branch names must be existing
local branches; one analysis at a time; clear error messages, no tracebacks;
outputs carry no generated test code or raw test output, and configured API
keys are scrubbed from every string.

Setup (its own venv: `mcp` 2.x needs newer pydantic/starlette than the
FastAPI backend pins):

```bash
uv venv .venv-mcp --python 3.12
uv pip install --python .venv-mcp/bin/python -r backend/mergeguard_mcp/requirements.txt
```

The server reads `NEBIUS_API_KEY` from its environment. To keep the key out of
client configs, point `MERGEGUARD_ENV_FILE` at a dotenv file; only `NEBIUS_*`
entries are read from it. Without a key, scores come from the git heuristic
and nothing is verified (the result says so in `note`).

Cursor (`.cursor/mcp.json` in your project, or `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "mergeguard": {
      "command": "/ABS/PATH/mergeguard/.venv-mcp/bin/python",
      "args": ["/ABS/PATH/mergeguard/backend/mergeguard_mcp/server.py"],
      "env": {
        "MERGEGUARD_ENV_FILE": "~/.config/mergeguard/nebius.env",
        "VERIFY_ALLOWED_ROOTS": "/ABS/PATH/to/your/repos"
      }
    }
  }
}
```

Claude Code:

```bash
claude mcp add mergeguard \
  -e MERGEGUARD_ENV_FILE=~/.config/mergeguard/nebius.env \
  -e VERIFY_ALLOWED_ROOTS=/ABS/PATH/to/your/repos \
  -- /ABS/PATH/mergeguard/.venv-mcp/bin/python /ABS/PATH/mergeguard/backend/mergeguard_mcp/server.py
```

End-to-end check over real stdio with the official MCP client
(`backend/scripts/mcp_e2e.py`, full JSON in `backend/.workspace/mcp-e2e/`).
Measured on 2026-10-08, first call per repo without cache:

| repo | tool | time | result | cost |
|---|---|---|---|---|
| checkout demo | `check_merge_risk(feat/tax-before-discount)` | 23.2 s | confirmed conflict with `feat/discount-cap` (high, 90) | $0.0052 |
| checkout demo | `list_conflicting_branches` | 3.3 s | 1 risky pair of 3, 1 confirmed (cached) | $0 |
| checkout demo | `suggest_merge_order` | 3.1 s | `docs/readme`, then `feat/discount-cap` alone, then rebase `feat/tax-before-discount` | $0 |
| daimon real case | `check_merge_risk(pr-17-deploy-review-findings)` | 78.3 s | confirmed conflict with `pr-14-sync-fixes` (high, 90), static break at `test_branding.py:77` | $0.0214 |
| daimon real case | `list_conflicting_branches` / `suggest_merge_order` | 15.7 s each | 1 of 1 confirmed; merge #14 first, then rebase #17 (cached) | $0 |
| guardrails | `/etc`, unknown branch | 0.0 s | `is_error` with a clear message | $0 |

## Tests

```bash
cd backend && source .venv/bin/activate && pytest -q
```

The test suite builds a throwaway git repo with intentionally overlapping branches
and asserts the Sentinel flags them.

## Repo layout

```
backend/            FastAPI service
  app/
    main.py         entrypoint + CORS
    config.py       env-driven settings (Nebius/Tavily keys)
    schemas.py      API models
    api/routes.py   /api/health (status, model, provider), /api/analyze
    services/
      git_service.py  pure-git diff/branch helpers (subprocess)
      nemotron.py     Token Factory chat-completions client
      sentinel.py     conflict-risk scoring (git, optional Nemotron blend)
      prefilter.py    static symbol/call prefilter before any LLM call
      cache.py        on-disk Nemotron result cache
      verifier.py     verify-by-test (generated pytest in throwaway worktrees)
  tests/
frontend/           dashboard (to be scaffolded)
docs/               customer discovery + notes
```

## Why "MergeGuard"

Something is always on guard at the merge point — watching every in-flight
branch so a conflict never lands as a surprise.
