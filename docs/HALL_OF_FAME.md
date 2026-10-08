# Semantic Conflict Hall of Fame

Real semantic merge conflicts from public repos: two pull requests that each
passed CI, merged with **no textual conflict**, and broke `main` together. Each
case is pinned to exact SHAs and replayable with one command; every number
below comes from a fresh MergeGuard run (no cache) on 2026-10-08 with
`nvidia/nemotron-3-super-120b-a12b` on Nebius.

**Admission rule:** a case is listed here only if MergeGuard's default pipeline
(prefilter -> model triage -> verify-by-test on high-risk pairs) returned
**confirmed** for the PR pair, i.e. a generated test passed on its own branch
and failed on the merge. Cases we reproduced but MergeGuard did not confirm are
listed under [Investigated, not confirmed](#investigated-not-confirmed).

Every replay (`python3 demo/replay.py --case NAME --prove`) checks, and printed
on our runs:

- base, PR A and PR B pass the case's tests;
- `git merge --no-ff --no-commit` of the two PR heads is clean;
- A+B fails;
- A+B plus the upstream fix commit passes.

Times are Europe/Copenhagen (CEST). Costs are the engine's own token-based
estimate ($0.30 / $0.90 per 1M input/output tokens). Re-run the measurements with
`cd backend && PYTHONPATH=. python scripts/eval_reliability.py --cases real-case-1,kiss --runs 3`
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

**MergeGuard: confirmed 3/3.**

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

**MergeGuard: confirmed 2/6. This case is flaky.**

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

## Investigated, not confirmed

| Case | Reproduced? | Why it is not in the Hall of Fame |
|---|---|---|
| elastic/observability-migration-platform [#381](https://github.com/elastic/observability-migration-platform/pull/381) x [#383](https://github.com/elastic/observability-migration-platform/pull/383) (merged 2026-08-31 19:31:46 / 19:32:03; fix `1dcac99`, "test(grafana): refresh the stale bargauge nested-aggregation expectation", via #387 at 2026-09-01 07:52:39). Replay: `python3 demo/replay.py --case elastic --prove` | Yes: base, A and B pass; clean merge; A+B fails `test_bargauge_reduces_over_the_collapsed_total`; the fix passes | MergeGuard did not confirm it (0/6). The prefilter found `nested_agg_family_rule` changed by both PRs, but the model scored 45 (five runs) and 20 (one run), below the 70 verify threshold, so verify never ran. Cost $0.0075 for all 6 runs. A diagnostic run with verify forced (not the default pipeline) gave suspected 3/3: no generated test passed on both branches within 4 attempts ($0.1563). The break is an exact query-text assertion in #383's new test, which generated intent tests did not recreate. |
| QwenLM/qwen-code [#8947](https://github.com/QwenLM/qwen-code/pull/8947) x [#8933](https://github.com/QwenLM/qwen-code/pull/8933) (TypeScript; merged 2026-08-12 06:15:47 / 2026-08-11 19:21:04; fix `ec3b2cbb` via #8984 at 2026-08-12 08:39:34). Replay: `python3 demo/replay.py --case qwen-code --prove` (npm ci + core build + vitest) | Yes, on Linux: `bridge.test.ts` has 544 / 557 / 553 passing tests on base / A / B; the merge is clean; A+B fails "transport failure marks the channel dying before process exit"; the fix passes all 566 | Verify-by-test is Python-only, so the pair can never be confirmed today ("skipped: no Python changes on either branch"). The model flagged it high (85) in 3/3 runs ($0.0043), but for the wrong reason: it predicted a textual conflict in an integration-test harness and did not name the real cause (`historyPageSize` only takes effect with `historyReplay: 'response'` since #8933). |
| pnpm/pnpm (fix `6a590dea`, TS2741 in `createDeployFiles.test.ts`) | Not attempted | TypeScript compile error; verify is Python-only. |
| votingworks/vxsuite #9264 x #9232 (fix `e1bbf43f`) | Not attempted | TypeScript monorepo; verify is Python-only. |
| misofm/engine #1024 x #1042 (fix `5651861f`) | Not attempted | Rust; verify is Python-only. |
| huglabs/brainskit (fix `239523e`, "test: test_filelock imports the harness first") | No | Merging the two sides gives a textual conflict in `tests/test_onboarding.py`, so it is not a clean semantic case. |
| EarthSciML/EarthSciAST (fix `1ea18ee1`, module rename `pde_inline_tests` -> `inline_tests`) | No | Merging the branch with main or with #210's head gives textual conflicts in `problem.py` and a Rust test. |
| jackmcarthur/lorrax (fix `1e97e02a`) | No | Not from two PRs: a rebased branch. It also has no license and heavy JAX/h5py dependencies. |
| headroomlabs-ai/headroom #2306 | No | Its "semantic merge conflict" was a later PR (#2198) breaking an assertion from a much older PR (#1433), not two concurrent PRs. |
| skornehl/ha-google-photos-backup (fix `c81ce810`) | No | The only failure was a ruff F821 lint error; no test fails. |

## What these replays taught us about MergeGuard

- **Triage is the bottleneck.** In 3 of the 4 reproduced cases the evidence
  MergeGuard needed was in its own prefilter output: `read_floor`,
  `nested_agg_family_rule` and the `PurgePreview` call site. When verify ran
  on KISS, it confirmed 4 of 6 times (2 of 3 normal, 2 of 3 forced).
- **The static signature check compares parameters, not return values.** The
  prompt's line "(no signature change between a changed definition and its
  callers)" is what the model quoted in its three KISS misses.
- **The model is not deterministic at temperature 0.** The same 2,368-token
  KISS prompt scored 0, 0, 0 and later 95, 85, 85.
- **Verify-by-test is Python-only.** Most 2026 public examples we found are in
  TypeScript or Rust.
