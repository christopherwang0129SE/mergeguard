# MergeGuard

MergeGuard catches branches that merge cleanly in Git but break together. Sentinel compares in-flight branches, uses NVIDIA Nemotron 3 Super on Nebius Token Factory to explain semantic risk, and checks the merged result with branch tests and generated intent tests. The dashboard and MCP tools return the risky pair and its test evidence. Built for the Nebius x NVIDIA hackathon; Tavily grounds model prompts with library documentation.

## Run it in 2 commands

Prerequisites: Python 3.11+, Node.js 22, Git 2.38+ and Make, on Linux or macOS.
From the repository root:

```bash
make setup
make dev
```

Open http://localhost:3000. Setup installs backend and frontend dependencies and
creates both demo repositories. Dev starts FastAPI on `127.0.0.1:8000` and Vite on
`127.0.0.1:3000`; Ctrl-C stops both. Those ports must be free.
No keys are required for git scoring and deterministic branch tests.
Optionally copy `.env.example` to `backend/.env` if you do not already have one,
then set `NEBIUS_API_KEY` and optionally `TAVILY_API_KEY`. Never commit keys.

## Run the demo scenario

After setup, no server is needed:

```bash
make demo
```

This regenerates `demo/checkout-demo` and analyzes `feat/discount-cap` together
with `feat/tax-before-discount` through the same function as `POST /api/analyze`.
Git merges them cleanly, but the merged checkout computes **200 instead of 198**
for the capped-discount test. With no keys, branch tests prove the regression
and raise the pair to **90/100, high**. With an enabled Nebius key, Nemotron also
assesses risk and eligible pairs get verify-by-test. The report names the proof
path actually used, including a deterministic fallback if generation does not
confirm. Exit code 0 means the pair was flagged and proven; otherwise it is nonzero.
To force a keyless run even with a configured dotenv file:
`NEBIUS_API_KEY= TAVILY_API_KEY= make demo`.

In the dashboard, click **Try demo repo** for the same fixture (plus a harmless
docs branch). **Try API demo (Tavily)** uses third-party libraries and exercises
documentation lookup when both provider keys are configured.

## Where Nebius / Nemotron is used

`backend/app/services/nemotron.py` calls NVIDIA Nemotron 3 Super
(`nvidia/nemotron-3-super-120b-a12b`) through Nebius Token Factory's OpenAI-compatible
API. `sentinel.py` requests risk assessments after the static prefilter;
`verifier.py` requests pytest intent tests, executes them in throwaway worktrees,
and can repair them using test feedback. No enabled key means no model calls.

## Where Tavily is used

`backend/app/services/docs_lookup.py` finds third-party imports and relevant
symbols in changed code, searches library documentation, and supplies bounded
snippets to the risk and intent-test prompts. Source links appear in the
dashboard and MCP results. Missing keys or failed searches do not block analysis.
The checkout demo uses only the standard library, so it does not trigger Tavily;
the API demo uses `requests` and `pydantic`.

## Architecture

```text
Local branches -> Sentinel: git diff + static symbol/call prefilter
                   |-> Branch tests on merge (no key) -----------|
                   |-> Nemotron risk on Nebius Token Factory    |
                   |       + Tavily library docs                |
                   |       -> Verify-by-test (+ Tavily docs) ---|
                   |                                            v
                   +----------------------------> Scores + test evidence
                                                       |          |
                                                    FastAPI    MCP server
                                                       |       (in-process)
                                                    Dashboard
```

Both test layers use throwaway worktrees and execute only for allowed local
repositories. Branch tests run before model scoring; generated tests run only
for eligible high-risk pairs. FastAPI and the stdio MCP server share the engine;
the MCP server does not need a running HTTP server.

## The three agents

| Agent | Role | Status |
|-------|------|--------|
| **Sentinel** | *Predict* — scores conflict risk between every pair of in-flight branches from git hunk overlap, then optionally refines the riskiest pairs with NVIDIA Nemotron on Nebius Token Factory. | ✅ MVP |
| **Planner** | *Prevent* — decomposes work into non-overlapping "zones" and suggests merge/rebase order. | ⏳ planned |
| **Merger** | *Resolve* — semantic 3-way merge; uses **Tavily** to check current library docs, runs tests, explains the resolution. | ⏳ planned |

- **LLM:** NVIDIA Nemotron 3 Super (`nvidia/nemotron-3-super-120b-a12b`) on Nebius Token Factory, an OpenAI-compatible API at `https://api.tokenfactory.nebius.com/v1/`
- **Library docs:** Tavily, called at runtime by Sentinel and verify-by-test
- **Backend:** FastAPI (Python) · **Frontend:** dashboard (verdict, per-branch risk level, pair cards)

## How Nebius Token Factory and Tavily are used

**Nebius Token Factory** serves NVIDIA Nemotron 3 Super (`nvidia/nemotron-3-super-120b-a12b`). MergeGuard does not call another model. Two jobs use it:

- **Pair risk explanations.** After the static prefilter, Sentinel sends up to `NEBIUS_MAX_PAIRS` pairs to Nemotron and blends the model's `risk_score` with the git score. Details are under [Nemotron risk refinement](#nemotron-risk-refinement).
- **Intent tests.** Verify-by-test asks Nemotron to write one pytest module for a high-risk pair, then runs it in throwaway worktrees. Details are under [Verify-by-test](#verify-by-test-intent-tests-semantic-conflicts-with-evidence).

Each chat-completions call is a single attempt: the client sets `max_retries=0` and waits `NEBIUS_TIMEOUT_SECONDS` (default 40). Verify-by-test can still make several generations when a test fails on its own branch (`VERIFY_MAX_ATTEMPTS` in `.env.example` is 3). A rationale that contradicts the static signature checks gets one repair call. The response `usage` block prices prompt and completion tokens with `NEMOTRON_INPUT_USD_PER_1M` / `NEMOTRON_OUTPUT_USD_PER_1M` (defaults 0.30 / 0.90). Risk results are cached on disk under `WORKSPACE_DIR/nemotron-cache/`. Generated tests are cached under `WORKSPACE_DIR/verify-cache/`. An empty `NEBIUS_API_KEY`, or `NEBIUS_ENABLED=false`, keeps the git score and skips both jobs. The HTTP request still succeeds.

The Planner and Merger agents are still planned. Token Factory is not used to produce a semantic merge.

**Tavily** looks up current library documentation and adds it to those two prompts. The lookup lives in the Sentinel / verify engine. Earlier notes that put Tavily only on the future Merger are out of date.

Both call sites are on the model path. An empty `NEBIUS_API_KEY` or `NEBIUS_ENABLED=false` skips Tavily as well, because Sentinel never builds those prompts. On a Nemotron cache miss, and again when verify-by-test builds a test prompt, `docs_lookup` reads the pair's changed Python and JavaScript/TypeScript. It keeps third-party imports and drops the Python standard library, Node built-ins, relative imports, and modules that belong to the repo. It sends at most two searches (one per library, at most two symbols each) as an httpx `POST` to `https://api.tavily.com/search`, with `TAVILY_TIMEOUT_SECONDS` (default 8) and `TAVILY_MAX_RESULTS` (default 3). A query names symbols from the conflicting lines: `from fastapi import Depends` becomes `fastapi Depends documentation`, and a call such as `requests.get(...)` or `session.mount(...)` after `requests.Session()` becomes `requests get documentation` or `requests Session.mount documentation`. For JavaScript, `import { useEffect } from 'react'` becomes `react useEffect documentation`. Names and attributes on added or removed lines win over imports the hunk does not use. A bare import with no symbol stays `<library> documentation`. For `requests`, `fastapi`, `pydantic`, and `react`, the request sets `include_domains` to the official docs host (`requests.readthedocs.io`, `fastapi.tiangolo.com`, `docs.pydantic.dev`, `react.dev`). Other libraries are not domain-filtered. A hit whose title and content do not mention the library is dropped. Dropping every hit is not an error. Snippets are trimmed to 700 characters each and 2000 characters in total, then placed under the heading `Library documentation (via Tavily web search)`. A missing key, `TAVILY_ENABLED=false`, or no third-party import leaves the prompt unchanged. A failed search is logged and dropped. Snippets from a search that did succeed are still added. Analysis still finishes.

Hits are returned as `docs_sources` (`title`, `url`, `query`) on the pair and, when verify-by-test searched, on `verification` as well. The snippet body stays in the prompt. The MCP tools include the same list. The dashboard pair card lists them under "Library docs", sublabel "found by Tavily" (http(s) links, up to 6). The verdict's Nemotron line says how many grounded the review ("grounded with N library docs via Tavily").

Search results are cached in process, per query, for the life of the process. A disk-cache hit on the risk score restores the `docs_sources` stored with that assessment, with no new Tavily search and no Nemotron call. A cache file written before that field existed still loads and yields an empty list. Verify-by-test can still search when it builds a test prompt and attach its own list.

The bundled checkout demo imports only the Python standard library, so Tavily stays idle there even with a key. The bundled API demo (`demo/api-demo/`, `python3 demo/make_api_demo_repo.py`) is the one that triggers it: the conflicting branches change a `requests` client (`Session`, `session.mount`, `session.get(..., timeout=...)`) and a pydantic `field_validator`, so the pair card shows "Library docs" / "found by Tavily". Any other repo whose changed files import a third-party library triggers it the same way. A live end-to-end run on a small repo that imported `requests` returned `docs_sources` from requests.readthedocs.io and related pages. The opt-in smoke test is `test_live_tavily_search_for_requests` (see [Tests](#tests)).

`/api/health` reports `tavily_ready` (key present and `TAVILY_ENABLED`).

| Variable | Role |
|----------|------|
| `TAVILY_API_KEY` | Search key. Empty skips lookup. Never commit it. |
| `TAVILY_ENABLED` | `false` skips Tavily even when a key is set. Default `true`. |
| `TAVILY_TIMEOUT_SECONDS` | Seconds for one search request. Default 8. |
| `TAVILY_MAX_RESULTS` | Results requested per search. Default 3. Snippets are then trimmed. |

## Setup

Hosted demo (Vercel + Railway): [docs/DEPLOY.md](docs/DEPLOY.md).

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Only if backend/.env does not already exist:
test -e .env || cp ../.env.example .env
```

Edit `backend/.env`. Set `NEBIUS_API_KEY` for Nemotron. `TAVILY_API_KEY` is optional: leave it empty and library-doc lookup stays off. Never commit `backend/.env`.

```bash
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the API. Git scoring works with both keys empty.

### Demo repos for the dashboard

Both folders are gitignored. Create them before clicking the matching button. If a folder is missing, `POST /api/analyze` returns 400 and the dashboard shows the detail, which says the path is not a git repository.

| Button | Create | What a judge sees |
|--------|--------|-------------------|
| **Try demo repo** | `python3 demo/make_demo_repo.py` | `demo/checkout-demo/`. A checkout module. Two branches disagree about discount and tax. Git merges them cleanly. Standard library only, so Tavily does not search. |
| **Try API demo (Tavily)** | `python3 demo/make_api_demo_repo.py` | `demo/api-demo/`. A `requests` client and a pydantic model. Two branches disagree about cents versus dollars. The changed lines name `session.mount`, `session.get`, and `field_validator`, so Tavily fills "Library docs" / "found by Tavily" on the pair card. |

```bash
python3 demo/make_demo_repo.py
python3 demo/make_api_demo_repo.py
```

Each branch passes `python3 -m unittest discover -s tests` on its own. Merging the two feature branches leaves no textual conflict and fails tests. `python3 demo/make_demo_repo.py --verify` and `python3 demo/make_api_demo_repo.py --verify` check that. The API demo's tests stub `requests` and `pydantic` when those packages are not installed, and they never open a socket. See `demo/README.md`.

### Frontend

Node.js 22 is recommended.

```bash
cd frontend
npm ci
npm run dev
```

The dashboard is at http://localhost:3000 and calls the API at `http://localhost:8000` (`VITE_API_BASE_URL` overrides the API origin).

### Try the Sentinel on any repo

```bash
curl -s -X POST http://localhost:8000/api/analyze \
  -H 'content-type: application/json' \
  -d '{"repo_path": "/path/to/some/repo", "base_branch": "main"}' | python3 -m json.tool
```

Returns a per-branch diff summary and a ranked list of branch-pair **conflict-risk
scores** with human-readable rationales.

### Nemotron risk refinement

With no API key, initial scores come from git hunk overlap (including a 3-line
adjacency window). With verification requested for an allowed local repo, a
confirmed branch test can still raise the displayed score and level.
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
few recent commit subjects. When Tavily returned snippets, a
`Library documentation (via Tavily web search)` section is inserted before
the diff excerpts. With no snippets that section is omitted and the rest of
the prompt is unchanged.
It must reply with JSON
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
   `obj.total()` only matches a changed *method*. TypeScript/JavaScript uses a
   light scanner (`ts_symbols.py`, no TS parser): strings and comments are
   masked, declarations get brace-matched spans (functions, arrow functions,
   classes, class and object-literal methods, interfaces/types) and a change
   is attributed to the innermost one; a thin wrapper around a changed
   declaration counts too (`loadSession` "via" `restoreSession`); calls
   resolve through named, namespace and package imports and through
   `export { x } from` / `export * from`. Other languages use a
   `def`/`function`/`func`/`fn` regex heuristic matched by name. An overlap
   needs one branch to have CHANGED the definition (both changed it, or one
   changed it and the other calls it); calls both branches make to a function
   neither changed never count. A pair is a model candidate only if the
   branches share a file or have such an overlap; other pairs get
   `fallback_reason: "prefiltered"` and never reach the model. Each pair
   carries `symbol_overlap` and `symbol_evidence` (who changed it, who calls
   it, in which file), calls from the other branch's tests first.
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

### Branch tests on merge (deterministic, no LLM)

Each branch usually ships tests for its own change, and a semantic merge
conflict very often shows up as one of those tests failing once the other
branch lands. For every prefilter-candidate pair with a clean textual merge,
Sentinel collects the test files each branch **added or modified** since the
merge-base, runs A's on A, B's on B and both sets on the merge of A+B, in the
same sandbox as verify-by-test below (throwaway worktrees, the repo's own
`.venv`, stripped environment, no network, timeout). This runs before and
independently of the model triage: no LLM call, $0, and a low model score
cannot suppress it.

Python test files run with pytest. JS/TS test files (`*.test.ts`,
`*.spec.tsx`, ...) run with the package's own **vitest or jest**:

- The package manager comes from the root lockfile (`package-lock.json`,
  `pnpm-lock.yaml`, `yarn.lock`), the runner from the nearest `package.json`
  that declares vitest or jest (dependency, `test` script or config file).
- Dependencies are installed **once per lockfile** into a shared cache
  (`WORKSPACE_DIR/js-deps/`): only the manifests are copied, lifecycle
  scripts are stripped and disabled (`npm ci --ignore-scripts`,
  `pnpm install --frozen-lockfile --ignore-scripts`, yarn equivalents). This
  is the only step with network access; it runs outside the test sandbox,
  with the stripped environment, only for repos under the allowed roots.
  npm fingerprints ignore the workspace packages' own version fields, so a
  release bump does not force a reinstall.
- Each worktree gets `node_modules` symlinked into that install (workspace
  packages point at the worktree's own code). Only workspace packages that
  the tested package imports through a missing build output (`dist/...`) are
  built, inside the sandbox, capped at `BRANCH_TESTS_JS_BUILD_SECONDS` (300)
  per pair, and cached by their git tree hash.
- Tests run per package with the runner's JSON reporter, inside the sandbox
  (macOS `sandbox-exec`: localhost only, writes only in the temp dir) plus a
  `--require` network guard (no TCP or DNS beyond loopback).
- At most `BRANCH_TESTS_JS_MAX_PACKAGES` test packages per pair (3; packages
  touched by both branches first). `branch_tests.setup`, `install_s` and
  `build_s` report what the install and builds cost.

`pairs[].branch_tests.status` is:

- `confirmed`: a branch's own test passes on its branch and fails on A+B,
  twice (a test that passes on the re-run is dropped as flaky), and does not
  already fail on the merge-base. Evidence: `confirming[]` with the test id,
  whose test it is and a short error excerpt. Authoritative like an
  intent-test confirmation: level `high`, score floored at
  `VERIFY_CONFIRMED_FLOOR`, `score_note` `"raised: confirmed by branch test"`
  (or `"... by branch test and intent test"` when both layers confirm). The UI
  labels it **Confirmed by branch test** (sublabel "no LLM"), separate
  from **Confirmed by intent test** (sublabel "written by Nemotron").
- `passed`: ran, no such test. `skipped`: no test files changed, or a textual
  merge conflict. `unsupported`: only test files that cannot run (JS/TS with
  no vitest/jest setup, no `node`, or `BRANCH_TESTS_JS_ENABLED=false`).
  `error`: could not run (for example a timeout or a failed install).

Limits: at most `BRANCH_TESTS_MAX_PAIRS` pairs (default 6, highest prefilter
signal first), `BRANCH_TESTS_MAX_FILES` test files per branch (20),
`BRANCH_TESTS_TIMEOUT_SECONDS` per pytest / vitest / jest run (120),
`BRANCH_TESTS_JS_INSTALL_SECONDS` for a one-time dependency install (900) and
`BRANCH_TESTS_TOTAL_SECONDS` for the whole stage (600; later pairs are
skipped). It runs only where verify-by-test may run (local repos under
`VERIFY_ALLOWED_ROOTS`, `"verify": true`). `BRANCH_TESTS_ENABLED=false` turns
it off.

### Verify-by-test: intent tests (semantic conflicts, with evidence)

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
(`VERIFY_MAX_ATTEMPTS`; the Settings default is 4, and `.env.example` sets 3). If Nemotron's
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
- Generated code that imports low-level network or process modules (`socket`,
  `subprocess`, `urllib`, and the like) or that calls `open`, `eval`, `exec`,
  `os.system` and similar is rejected before it runs. `requests`, `httpx`,
  `aiohttp`, and `urllib3` are allowed only when the repository declares that
  dependency. The socket guard still blocks the connection.
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
python3 demo/replay.py --case real-case-1 --prove   # demo/replay_case_1.py still works
```

More real cases, and the ones MergeGuard did not confirm, are in the
[Semantic Conflict Hall of Fame](docs/HALL_OF_FAME.md)
(`python3 demo/replay.py --list`). With the branch-test stage, the current
pipeline confirms daimon, KISS and elastic 3/3 each (daimon by both layers,
KISS and elastic by the branch test only) and, since the branch-test stage
runs vitest/jest, the TypeScript qwen-code pair 3/3 (branch test only; the
intent-test layer is still Python-only).

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

After adding the branch-test stage and the corrected prompt line (`risk-v8`),
re-measured on 2026-10-08, 3 runs per case. "Branch" and "intent" count the
pairs each layer confirmed; "confirmed" is either layer:

| case | runs | pairs | confirmed | by branch test | by intent test | branch-test status | median time (s) | tokens | $ |
|---|---|---|---|---|---|---|---|---|---|
| real case (daimon #14 x #17) | 3 | 3 | 3 | 3 | 3 | confirmed 3 | 85.9 | 79,040 | 0.0405 |
| KISS #311 x #315 | 3 | 3 | 3 | 3 | 0 (suspected 3) | confirmed 3 | 110.4 | 241,807 | 0.1009 |
| elastic #381 x #383 | 3 | 3 | 3 | 3 | 0 (not run: score < 70) | confirmed 3 | 17.4 | 8,484 | 0.0033 |
| qwen-code #8947 x #8933 (TypeScript) | 3 | 3 | 0 | 0 | 0 (skipped) | unsupported 3 | 9.1 | 12,305 | 0.0056 |
| checkout demo, conflicting pair | 3 | 3 | 3 | 3 | 3 | confirmed 3 | 30.1 | 47,135 | 0.0285 |
| checkout demo, docs pairs | 3 | 6 | 0 | 0 | 0 | not run 6 (not candidates) | (same runs) | | |
| harmless fixture | 3 | 18 | 0 | 0 | 0 | passed 9, not run 9 | 10.6 | 11,388 | 0.0050 |
| harmless fixture, both layers forced on every pair | 3 | 18 | 0 | 0 | 0 (suspected 18) | passed 15, skipped 3 | 293.3 | 275,569 | 0.1645 |

Harmless pairs: 42 pair-runs, **0 false confirmations** (branch test 0,
intent test 0). Total spend for this re-measure: $0.3484. The branch-test
stage adds no LLM cost; it took 12.9-33.6 s per run on the three Python
replays.

After adding JS/TS branch tests and the TypeScript prefilter (prompt
`risk-v9`), re-measured on 2026-10-08 (raw results
`backend/.workspace/eval/20261008-203026/` and `.../20261008-203912/`). The
JS dependency cache was deleted first, so run 1 of qwen-code includes the
one-time npm install:

| case | runs | pairs | confirmed | by branch test | by intent test | branch-test status | time per run (s) | JS install / builds (s) | tokens | $ |
|---|---|---|---|---|---|---|---|---|---|---|
| qwen-code #8947 x #8933 (TypeScript) | 3 | 3 | **3** | 3 | 0 (skipped: Python-only) | confirmed 3 | 253.2, 136.3, 136.0 | 29.3 / 89.0 in run 1; 0 / 0 (cached) in runs 2-3 | 12,659 | 0.0055 |
| checkout demo, conflicting pair | 1 | 1 | 1 | 1 | 1 | confirmed 1 | 22.4 | - | 9,472 | 0.0049 |
| checkout demo, docs pairs | 1 | 2 | 0 | 0 | 0 | not run 2 | (same run) | - | | |
| harmless fixture | 1 | 6 | 0 | 0 | 0 | passed 3, not run 3 | 10.2 | - | 3,815 | 0.0017 |
| harmless fixture, both layers forced on every pair | 1 | 6 | 0 | 0 | 0 (suspected 6) | passed 5, skipped 1 | 281.7 | - | 89,078 | 0.0521 |

Harmless pairs: 14 pair-runs, **0 false confirmations**. Spend: $0.0642. The
qwen-code branch-test stage took 235.5 s in run 1 (npm install 29.3 s,
workspace builds 89.0 s: `packages/core`, `packages/acp-bridge`,
`packages/sdk-typescript`) and 115.8 s / 115.3 s with the cache warm; the
rest is 11 vitest runs (3 packages x A, B, A+B, plus the merge-base and a
re-run of the failing file). The harmless fixtures are Python; the JS/TS
harmless pair is covered by `tests/test_js_branch_tests.py` (real git, real
node, a stand-in runner) and `tests/test_ts_prefilter.py`.

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
entries are read from it. Without a key, initial scores come from the git heuristic and generated intent
tests are skipped. Branch tests can still confirm regressions on allowed local
repos when verification is requested.

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
claude mcp add mergeguard --scope local \
  -e MERGEGUARD_ENV_FILE=~/.config/mergeguard/nebius.env \
  -e VERIFY_ALLOWED_ROOTS=/ABS/PATH/to/your/repos \
  -- /ABS/PATH/mergeguard/.venv-mcp/bin/python /ABS/PATH/mergeguard/backend/mergeguard_mcp/server.py
```

`.cursor/mcp.json` holds absolute local paths, so it is gitignored. Every
tool sends an MCP progress notification when it starts and then every 5 s
while it runs (a fresh analysis takes 20-80 s), for clients that show progress
or reset their request timeout on it.

Checked in real clients on 2026-10-08: Claude Code 2.1.282 (`claude mcp get
mergeguard`: Connected, local scope) and Cursor CLI 2026.08.11
(`cursor-agent mcp list-tools mergeguard`: the three tools with their
arguments), both launching the server with exactly the config above.

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

### Sync open GitHub pull requests

Set `GITHUB_REPOSITORY` to `owner/repository` and `GITHUB_SEATED_USERS` to a
comma-separated list of seated GitHub usernames in `backend/.env`. Set
`GITHUB_TOKEN` for private repositories or higher API rate limits. The
`GET /api/github/pull-requests` endpoint syncs open pull requests targeting
`DEFAULT_BASE_BRANCH`, includes only authors in the seated-user list, fetches
their GitHub PR refs into the local workspace clone, and returns the PR details
alongside Sentinel's conflict-risk analysis against the latest fetched
`origin/DEFAULT_BASE_BRANCH`. Scoring uses the same pipeline as `/api/analyze`
(git heuristic, static prefilter, optional Nemotron). Generated intent tests
and branch tests stay off for these URL clones. It does not assign reviewers
or merge pull requests.

## Tests

Backend (git fixtures, mocked Nemotron, mocked Tavily):

```bash
cd backend && source .venv/bin/activate && pytest -q
```

The suite builds a throwaway git repo with intentionally overlapping branches
and asserts the Sentinel flags them. The live Tavily test is skipped unless
you opt in.

MCP tests use the separate venv from the [MCP server](#mcp-server-for-cursor-claude-code-and-other-agents) section (`mcp` is not in the FastAPI requirements). `tests/test_mcp_tools.py` does not import the SDK. `tests/test_mcp_server.py` does, and skips itself when `mcp` is missing.

```bash
cd backend && ../.venv-mcp/bin/python -m pytest -q tests/test_mcp_tools.py tests/test_mcp_server.py
```

Frontend (Vitest, via `npm test`):

```bash
cd frontend && npm ci && npm test
```

Opt-in live Tavily smoke test, from `backend/` with the backend venv active. The test name is `test_live_tavily_search_for_requests`. It looks up `import requests` and expects at least one snippet. The placeholder below is not a key. Do not commit a real one.

```bash
TAVILY_API_KEY=... MERGEGUARD_LIVE_TAVILY=1 python -m pytest -q tests/test_docs_lookup.py -k live
```

## Repo layout

```
backend/            FastAPI service
  app/
    main.py         entrypoint + CORS
    config.py       env-driven settings (Nebius/Tavily keys)
    schemas.py      API models
    api/routes.py   /api/health, /api/analyze
    services/
      git_service.py  pure-git diff/branch helpers (subprocess)
      github_service.py  open pull requests for seated authors (GitHub API)
      nemotron.py     Token Factory chat-completions client (Nemotron 3 Super)
      docs_lookup.py  Tavily library-doc lookup for the risk and verify prompts
      prefilter.py    static symbol/call prefilter before any LLM call
      ts_symbols.py   TypeScript/JavaScript declarations, imports, re-exports for the prefilter
      cache.py        on-disk Nemotron result cache
      verifier.py     verify-by-test (generated pytest in throwaway worktrees)
      branch_tests.py branch tests on merge (each branch's own tests on A+B, no LLM)
      js_tests.py     vitest/jest for branch tests: shared install per lockfile, minimal builds
      sentinel.py     conflict-risk scoring
  tests/
frontend/           React dashboard
docs/               customer discovery + notes
```

## Why "MergeGuard"

Something is always on guard at the merge point — watching every in-flight
branch so a conflict never lands as a surprise.
