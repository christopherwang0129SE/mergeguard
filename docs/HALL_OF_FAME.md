# Semantic Conflict Hall of Fame

Real semantic merge conflicts from public repos: two pull requests that each
passed CI, merged with **no textual conflict**, and broke `main` together. Each
case is pinned to exact SHAs and replayable with one command; every number
below comes from fresh MergeGuard runs (no cache) on 2026-10-08 with
`nvidia/nemotron-3-super-120b-a12b` on Nebius.

**Admission rule:** a case is listed here only if MergeGuard's default pipeline
returned **confirmed** for the PR pair. Two layers can confirm, and every case
says which one did:

- **Branch test** (deterministic, no LLM, $0): a test file that one PR added
  or modified passes on that PR, fails on the merge of A+B (twice), and does
  not already fail on the merge-base. It runs for every prefilter candidate
  pair, before and independently of the model score.
- **Intent test** (Nemotron): a test Nemotron generated for a high-risk pair
  passes on its own branch, fails on the merge-base and fails on the merge.

Cases we reproduced but MergeGuard did not confirm are listed under
[Investigated, not confirmed](#investigated-not-confirmed).

## Results (current pipeline, 3 fresh runs each)

Branch tests on. The three Python cases were measured with prompt `risk-v8`
(raw results `backend/.workspace/eval/20261008-191241/`); qwen-code was
re-measured after JS/TS branch tests and the TypeScript prefilter landed,
with prompt `risk-v9` (`backend/.workspace/eval/20261008-203026/`, JS
dependency cache deleted first, so run 1 includes the npm install).

| Case | Confirmed | Layer | Model score | Cost (3 runs) | Time per run |
|---|---|---|---|---|---|
| [daimon #14 x #17](#1-pymc-labsdaimon-a-new-required-field-vs-a-new-fixture) | **3/3** | branch test 3/3 and intent test 3/3 | 80, 85, 85 | $0.0405 | 113.8 s, 83.4 s, 85.9 s |
| [KISS #311 x #315](#2-thinkersjournalkiss-read_floor-starts-returning-a-tuple) | **3/3** | branch test 3/3 (intent test: suspected 3/3) | 90, 90, 90 | $0.1009 | 97.4 s, 110.4 s, 120.1 s |
| [elastic #381 x #383](#3-elasticobservability-migration-platform-a-third-query-stage-vs-a-two-stage-assertion) | **3/3** | branch test 3/3 (intent test never ran: score below 70) | 45, 30, 45 | $0.0033 | 17.3 s, 17.6 s, 17.4 s |
| [qwen-code #8947 x #8933](#4-qwenlmqwen-code-historypagesize-now-needs-historyreplay-response) (TypeScript) | **3/3** | branch test 3/3 (intent test: Python-only, skipped) | 88, 55, 45 | $0.0055 | 253.2 s (incl. npm install 29.3 s and builds 89.0 s), 136.3 s, 136.0 s |

The cost is the Nemotron triage plus intent tests; the branch-test stage
itself makes no LLM call. It took 33.5-33.6 s per run on daimon, 22.7-22.8 s
on KISS, 12.9 s on elastic and 235.5 s / 115.8 s / 115.3 s on qwen-code (cold /
warm dependency and build cache), all included in the times above.

Every replay (`python3 demo/replay.py --case NAME --prove`) checks, and printed
on our runs:

- base, PR A and PR B pass the case's tests;
- `git merge --no-ff --no-commit` of the two PR heads is clean;
- A+B fails;
- A+B plus the upstream fix commit passes.

Times are Europe/Copenhagen (CEST). Costs are the engine's own token-based
estimate ($0.30 / $0.90 per 1M input/output tokens). Re-run the measurements with
`cd backend && PYTHONPATH=. python scripts/eval_reliability.py --cases real-case-1,kiss,elastic,qwen-code --runs 3`
(needs `NEBIUS_API_KEY` in the environment).

## 1. pymc-labs/daimon: a new required field vs a new fixture

| | |
|---|---|
| Repo | [pymc-labs/daimon](https://github.com/pymc-labs/daimon) (Python, MIT) |
| PR A | [#14](https://github.com/pymc-labs/daimon/pull/14) "fix: keep local DB and Managed Agents state in sync" — head `1c4f6104`, merged 2026-07-27 03:07:25 |
| PR B | [#17](https://github.com/pymc-labs/daimon/pull/17) "fix: harden deploy workflows and validate bot display name" — head `7175c4d3`, merged 2026-07-27 01:35:40 |
| Base | `1933b244` (merge-base) |
| Fix | `239fdb55`, committed straight to `main` (no PR) 2026-07-27 03:09:48 |
| Replay | `python3 demo/replay.py --case real-case-1 --prove` |

**What broke.** #14 added two required fields to the pydantic model
`PurgePreview`; #17 added `test_branding.py`, whose fixture builds a
`PurgePreview` with the old field set. On the merge, 2 tests in
`test_branding.py` fail (pydantic ValidationError). The fix commit says:

> fix(tests): add new PurgePreview slack fields to branding test fixture
>
> Semantic merge conflict: PR #17 added test_branding.py while PR #14
> extended PurgePreview with slack_user_tokens and slack_turn_contexts;
> neither branch's CI saw the other.

**MergeGuard now: confirmed 3/3, by both layers.**

- Branch test (3/3): #17's own `test_branding.py` passes on #17 and fails on
  the merge: `TestPrivacyMainContainer::test_set_name_changes_text` and
  `::test_unset_matches_todays_text`, `ValidationError: 2 validation errors
  for PurgePreview | slack_user_tokens | Field required`. The file does not
  exist on the merge-base. #14 changed more than 20 test files; only the first
  20 ran (the per-branch cap), and most of them error on #14 itself in the
  sandbox (227 errors), so they cannot confirm anything.
- Intent test (3/3): confirmed in every run.
- Model score 80, 85, 85. Cost $0.0183, $0.0108, $0.0113; time 113.8 s,
  83.4 s, 85.9 s.

**Earlier (before the branch-test stage, prompt `risk-v7`): confirmed 3/3 by
the intent test.**

- Prefilter evidence: `PurgePreview` changed by #14, called by #17 in
  `test_branding.py`, plus 6 other symbol links.
- Static check: `PurgePreview` call site `test_branding.py:77` on #17 does
  not pass the new required `slack_user_tokens` and `slack_turn_contexts`.
- Model score: 70, 75 and 75 (high).
- Verify: confirmed on the first attempt each time (confirming test
  `test_branch_b_make_preview_works`).
- Cost: $0.0296 total, $0.0098–0.0100 per run.
- Time: 38.6 s, 37.1 s and 36.8 s per run.

## 2. ThinkersJournal/KISS: `read_floor` starts returning a tuple

| | |
|---|---|
| Repo | [ThinkersJournal/KISS](https://github.com/ThinkersJournal/KISS) (the conflict is in its Python `tools/`; CC0-1.0) |
| PR A | [#311](https://github.com/ThinkersJournal/KISS/pull/311) "conform: PROVEN batch 1 — nine mutation-verified markers + isolation matrix" — head `fc97ccc3`, merged 2026-08-21 19:59:22 |
| PR B | [#315](https://github.com/ThinkersJournal/KISS/pull/315) "tools: the ratchet's DIMENSION SET is now checked" — head `bef61e27`, merged 2026-08-21 20:00:24 |
| Base | `9174f952` (merge-base) |
| Fix | `9c8543bd`, [#321](https://github.com/ThinkersJournal/KISS/pull/321), merged 2026-08-26 08:53:06 |
| Replay | `python3 demo/replay.py --case kiss --prove` (stdlib + pytest) |

**What broke.** The two PRs touch no common file. #315 changed
`kiss_trace.read_floor()` to return `(floor, problems)`. #311 added a caller,
`_floor_proven()`, that still does `read_floor(...).get("proven", 0)`. On the
merge, `tools/test_kiss_proven_marker.py::test_live_markers_are_all_valid_and_match_the_floor`
fails with `AttributeError: 'tuple' object has no attribute 'get'`. The fix
commit says:

> fix(tools): main is RED — unpack read_floor's tuple in test_kiss_proven_marker (#321)
>
> A SEMANTIC MERGE CONFLICT BETWEEN TWO INDEPENDENTLY-GREEN PRs.
>
>   #311 (ee1d186) ADDED the caller `_floor_proven()` to test_kiss_proven_marker.py,
>         written against the dict signature that was current on its base.
>   #315 (d955e76) CHANGED read_floor to return a tuple, and updated the callers
>         that existed at the time — this one did not yet exist.
>
> Different files, so git merged them cleanly and neither PR could have seen it

Replay note: two test groups are deselected on every side because their
failures have nothing to do with the conflict:

- #315's `ValueIsComparedTest` needs an `origin/main` ref, which a local-only
  replay does not have;
- `test_kiss_ratchet_discrimination` fails identically on base, A and B on
  macOS but passes on Linux.

**MergeGuard now: confirmed 3/3, by the branch test.**

- Branch test (3/3): #311's own
  `tools/test_kiss_proven_marker.py::test_live_markers_are_all_valid_and_match_the_floor`
  passes on #311, passes on the merge-base and fails on the merge with
  `AttributeError: 'tuple' object has no attribute 'get'`, the upstream
  failure. 3 of #315's own tests already fail on #315 itself in the sandbox
  (the engine does not apply the replay's deselects), so they cannot confirm
  and are ignored.
- Intent test (0/3): the model scored 90 in all 3 runs, so verify ran, but
  each time it ended suspected (no generated test met the bar).
- Cost $0.0314, $0.0345, $0.0350; time 97.4 s, 110.4 s, 120.1 s.
- The model scored 90 in 3/3 runs with the corrected prompt line (below),
  versus 0, 0, 0 and 95, 85, 85 before. Three runs are too few to credit
  the prompt fix for that.

**Earlier (prompt `risk-v7`, no branch-test stage): confirmed 2/6. Flaky.**

- Prefilter evidence (every run): `read_floor` is changed by #315 in
  `tools/kiss_trace.py` and called by #311 in `tools/test_kiss_proven_marker.py`.
- Static check: no break. It compares parameters only; the change here is
  the return value.
- Batch 1 (3 runs): the model scored 0 each time ("static signature checks
  show no change to the function's signature"), so verify never ran.
- Batch 2 (3 runs), same prompt (2,368 tokens, temperature 0):
  - the model scored 95, 85 and 85, naming the dict→tuple return change;
  - verify returned confirmed, suspected (4 attempts, no generated test
    passed on both branches) and confirmed;
  - confirming tests: `test_branch_a_floor_proven_returns_expected` and
    `test_branch_a_floor_proven_intent`.
- Confirmed runs: $0.0219 / 88.1 s and $0.0345 / 85.8 s.
- All 6 runs: $0.0996 total.

## 3. elastic/observability-migration-platform: a third query stage vs a two-stage assertion

| | |
|---|---|
| Repo | [elastic/observability-migration-platform](https://github.com/elastic/observability-migration-platform) (Python) |
| PR A | [#381](https://github.com/elastic/observability-migration-platform/pull/381) — merged 2026-08-31 19:31:46 |
| PR B | [#383](https://github.com/elastic/observability-migration-platform/pull/383) — merged 2026-08-31 19:32:03 |
| Fix | `1dcac99` "test(grafana): refresh the stale bargauge nested-aggregation expectation", via #387, merged 2026-09-01 07:52:39 |
| Replay | `python3 demo/replay.py --case elastic --prove` |

**What broke.** #381 lowered nested aggregations through `TS` plus a time
bucket, so a summary panel gains a third `| STATS` stage. #383 added
`tests/test_issue382_nested_legend_inner_group.py`, whose
`test_bargauge_reduces_over_the_collapsed_total` unpacks exactly two stages
(`inner, outer = _stats_lines(...)`). Replay: base, A and B pass; the merge is
clean; A+B fails that test; the fix passes. The fix commit says:

> The breakage is a semantic merge conflict rather than a regression. #383's
> branch was cut before #381 merged [...] Merging #383 on top of #381 combined
> a passing test with a changed shape, which neither PR's CI could see.

**MergeGuard now: confirmed 3/3, by the branch test only.**

- Branch test (3/3): #383's own
  `SummaryPanelBranchTests::test_bargauge_reduces_over_the_collapsed_total`
  passes on #383 and fails on the merge with `ValueError: too many values to
  unpack (expected 2)`. 926 of #381's own tests (`tests/test_migrate.py`) pass
  on #381 and on the merge.
- Intent test: never ran. The model scored 45, 30 and 45, below the 70
  verify threshold.
- Cost $0.0011 per run; time 17.3 s, 17.6 s, 17.4 s.

**Earlier (prompt `risk-v7`, no branch-test stage): not confirmed, 0/6.** The
model scored 45 (five runs) and 20 (one run), so verify never ran ($0.0075 for
6 runs). A diagnostic run with verify forced gave suspected 3/3 ($0.1563):
no generated intent test recreated the break.

## 4. QwenLM/qwen-code: `historyPageSize` now needs `historyReplay: 'response'`

| | |
|---|---|
| Repo | [QwenLM/qwen-code](https://github.com/QwenLM/qwen-code) (TypeScript, npm workspaces, vitest) |
| PR A | [#8947](https://github.com/QwenLM/qwen-code/pull/8947) — merged 2026-08-12 06:15:47 |
| PR B | [#8933](https://github.com/QwenLM/qwen-code/pull/8933) — merged 2026-08-11 19:21:04 |
| Fix | `ec3b2cbb` via #8984, merged 2026-08-12 08:39:34 |
| Replay | `python3 demo/replay.py --case qwen-code --prove` (npm ci + core build + vitest) |

**What broke.** #8933 changed `restoreSession`, a function nested inside the
~8,800-line `createAcpSessionBridge` factory in
`packages/acp-bridge/src/bridge.ts`, so that `historyPageSize` only takes
effect when `historyReplay === 'response'`. #8947 added the test "transport
failure marks the channel dying before process exit" to
`packages/acp-bridge/src/bridge.test.ts`; it calls `bridge.loadSession({...,
historyPageSize: 10})` without `historyReplay`. The merge is clean and the
test fails on A+B. The fix adds `historyReplay: 'response'` to the test.
Replay (Linux): `bridge.test.ts` has 544 / 557 / 553 passing tests on base /
#8947 / #8933; A+B fails that test; the fix passes all 566.

**MergeGuard now: confirmed 3/3, by the branch test only.**

- Branch test (3/3): `bridge.test.ts` (changed by both PRs)
  `createAcpSessionBridge > transport failure marks the channel dying before
  process exit` passes on #8947 and fails on the merge, twice, with
  `AssertionError: expected [] to deep equally contain ObjectContaining{…}`.
  It does not exist on the merge-base (544 passed there). vitest on
  `packages/acp-bridge`: 553 passed on #8933, 631 on #8947 (3 files), 639
  passed and 1 failed on A+B. `packages/webui` (304 on A+B) and the root
  package were also run; 4 more packages with changed test files were not
  (cap 3 per pair).
- Setup: one `npm ci --ignore-scripts` into the shared cache (29.3 s, run 1
  only) and three workspace builds the tests import (`packages/core`,
  `packages/acp-bridge`, `packages/sdk-typescript`: 89.0 s in run 1, restored
  from the cache in runs 2 and 3). macOS `sandbox-exec`, localhost only.
- Prefilter: the top evidence is now `loadSession: changed by #8933
  (bridge.ts) via restoreSession, called by #8947 (bridge.test.ts)`, with a
  snippet showing the wrapper, the `historyPageSize` /
  `historyReplay === 'response'` change and the new call site. Before, the
  regex fallback named `Harness`, `deferred` and `resolve`.
- Model: 88, 55, 45. Every rationale names `restoreSession` and
  `historyPageSize`, but none states the actual break; runs 2 and 3 call the
  changes "compatible". The published score is 90 in all three runs because
  the branch test confirmed.
- Intent test: skipped ("no Python changes on either branch"); generated
  intent tests are still pytest only.
- Cost $0.0015, $0.0020, $0.0020 (the model triage only).

**Earlier (Python-only branch tests, `risk-v8`): not confirmed, 0/3** (model
85 in 3/3 runs, $0.0056). The branch-test stage reported "unsupported".

## Investigated, not confirmed

| Case | Reproduced? | Why it is not in the Hall of Fame |
|---|---|---|
| pnpm/pnpm (fix `6a590dea`, TS2741 in `createDeployFiles.test.ts`) | Not attempted | The fix commit does not name the two PRs, and the break is a type error (TS2741, `pnpm run compile-only`); the package's test script first compiles the pnpm CLI (`pn compile && pn --filter=pnpm compile`) and runs jest with `--experimental-vm-modules`. The branch-test stage does not type-check, does not run a package's `test` script and sets its own `NODE_OPTIONS`, so it would not see this break. |
| votingworks/vxsuite #9264 x #9232 (fix `e1bbf43f`) | Not attempted | Checked the setup only: `apps/admin/backend` depends on 15 workspace libraries (each needs a build) and on `@votingworks/db`, which uses the native `better-sqlite3`; with install scripts disabled (our rule) its binding is never built, so the tests cannot run on any side. The repo pins Node 24.19 and is 2.7 GB. |
| misofm/engine #1024 x #1042 (fix `5651861f`) | Not attempted | Rust; neither layer runs Rust tests (branch tests: pytest, vitest, jest; intent tests: pytest). |
| huglabs/brainskit (fix `239523e`, "test: test_filelock imports the harness first") | No | Merging the two sides gives a textual conflict in `tests/test_onboarding.py`, so it is not a clean semantic case. |
| EarthSciML/EarthSciAST (fix `1ea18ee1`, module rename `pde_inline_tests` -> `inline_tests`) | No | Merging the branch with main or with #210's head gives textual conflicts in `problem.py` and a Rust test. |
| jackmcarthur/lorrax (fix `1e97e02a`) | No | Not from two PRs: a rebased branch. It also has no license and heavy JAX/h5py dependencies. |
| headroomlabs-ai/headroom #2306 | No | Its "semantic merge conflict" was a later PR (#2198) breaking an assertion from a much older PR (#1433), not two concurrent PRs. |
| skornehl/ha-google-photos-backup (fix `c81ce810`) | No | The only failure was a ruff F821 lint error; no test fails. |

## What these replays taught us about MergeGuard

- **Triage was the bottleneck; the branches' own tests remove it.** In all
  three Python cases the PR that was broken by the merge shipped a test that
  catches the break. Running each PR's added/modified test files on A+B
  confirms all three, 3/3, with no LLM call, whatever the model scored
  (elastic: 45, 30, 45). The model stays as the explanation layer and the
  intent-test layer for pairs whose own tests do not cover the interaction.
- **Generated intent tests are the weaker layer on real repos.** They confirmed
  daimon 3/3 but KISS 0/3 this round (suspected each time, after the model
  had scored 90) and never recreated the elastic break.
- **The static signature check compares parameters, not return values.** The
  prompt used to say "(no signature change between a changed definition and
  its callers)" when nothing mismatched; the model quoted that line in its
  three KISS misses. It now says only parameters were compared and return
  values and behaviour were not checked (prompt `risk-v8`).
- **The model is not deterministic at temperature 0.** The same 2,368-token
  KISS prompt scored 0, 0, 0 and later 95, 85, 85 (`risk-v7`).
- **The branch-test layer now runs vitest and jest; the intent-test layer is
  still Python-only.** qwen-code went from 0/3 to 3/3 once its own vitest
  file ran on A+B. The cost is time, not money: a one-time npm install
  (29.3 s) and three workspace builds (89.0 s) on the first run, about 115 s
  per run after that. Repos whose tests need native modules built by install
  scripts (vxsuite) or a type check (pnpm) are out of reach.
- **The TypeScript prefilter now names the right symbol, the model still
  misjudges it.** With the evidence pointing at `loadSession` via
  `restoreSession`, the model scored qwen-code 88, 55 and 45 and called the
  changes compatible twice. The deterministic layer carries the case.
- **No false confirmations from the branch-test layer** on the harmless
  fixtures, including a stress run that forced it on every pair (see the
  README's reliability table).
