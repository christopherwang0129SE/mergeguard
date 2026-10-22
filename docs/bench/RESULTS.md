# MergeGuard real-repo benchmark

Replay of pairs of already-merged changes A and B from open-source repos. Each case asks whether A and B together break something. Figures below are copied from `docs/bench/report-summary.md` (report.py, 2026-10-22 03:01 CEST) and per-record checks of the result files for this run. Nothing here is recomputed.

## Summary

- 660 cases from 32 repos (`repos.txt` lists 40; 8 TypeScript repos yielded 0). 330 mined broke, 330 clean. Python 460, TypeScript 200.
- Final run: 628 ok, 32 replay_failed, 0 timeout, 0 error. replay_failed rate 32/660 = 4.8%. Error rate 0%.
- Of 628 ok: 317 mined broke, 311 mined clean (python 428, ts 200). Levels: low 400, high 136, medium 64, none 28. Flagged (level high or test-confirmed): 136 of 628 (python 88, ts 48). Test-confirmed: 2.
- Mined ground truth, flagged, n=628: TP 67, FP 69, FN 250, TN 242; precision 0.493, recall 0.211, specificity 0.778, F1 0.296.
- Hand labels show the mined follow-up-fix positives are mostly noise (3 of 49 hand-checked are real interactions, 35 unrelated; 11 unsure), so the mined precision/recall numbers do not measure real interaction detection. That is 3 of 49 (6%) real and 35 of 49 (71%) unrelated.
- Hand ground truth, n=349 ok cases (346 clean, 3 broke), flagged: TP 0, FP 73, FN 3, TN 273; precision 0.000, recall 0.000 (0 of 3), specificity 0.789. All 3 hand-confirmed interactions were missed. The hand sample has 3 positives, so this recall is not statistically meaningful.
- 106 of the 136 flagged cases have a textual merge conflict when A and B are replayed together.
- Total spend is $4.9031 of the $10 cap.

## Setup

`backend/scripts/bench/mine.py` mines broke and clean pairs from first-parent history. A mined broke case is a pair A, B whose later follow-up fix touched both. Confidence on those 330 broke cases: weak 313, medium 10, revert 7. Clean cases have confidence none (330): pairs that merged with no follow-up fix.

`backend/scripts/bench/replay.py` rebuilds one case as local branches `main`, `a`, and `b` with no remote. `main` is A's parent, and each change is applied onto `main`; when a squash-merged B does not apply there, replay falls back to B's PR head (read-only fetch of `refs/pull/N/head`). `backend/scripts/bench/run.py` is the budgeted, resumable per-case MergeGuard run (one child process per case). Flagged means level high, or a test confirmation (verification or branch tests).

`docs/bench/report-summary.md` is a copy of report.py output from 2026-10-22 03:01 CEST. The recorded command is:

```
cd backend
R=/workspace/mg-bench-repos/results L=/workspace/mg-bench-repos/labels
.venv/bin/python scripts/bench/report.py \
  --results $R/full2-a --results $R/full2-b --results $R/full2-c --results $R/full2-d --results $R/full2-e \
  --results $R/full2-f --results $R/full2-g --results $R/full2-h --results $R/full2-i --results $R/full2-j \
  --labels $L/pilot-hand-labels.csv --labels $L/full-hand-labels.csv \
  --src-root /workspace/mg-bench-repos/src --out-dir <out>
```

(The results live outside the repo, under `/workspace/mg-bench-repos/`; copies of the two hand-label files are in `docs/bench/labels/`.)

The script is `backend/scripts/bench/report.py`. It writes the markdown and CSV under `--out-dir`.

## Results

### Statuses

| status | n |
|---|---:|
| ok | 628 |
| replay_failed | 32 |
| timeout | 0 |
| error | 0 |

Of the 628 ok cases, 317 are mined broke and 311 are mined clean (python 428, ts 200).

| level | n |
|---|---:|
| low | 400 |
| high | 136 |
| medium | 64 |
| none | 28 |

Flagged (level high or test-confirmed): 136 of 628 (python 88, ts 48). Test-confirmed: 2.

### Mined ground truth

Flagged (high or test-confirmed), ok cases only:

| slice | n | TP | FP | FN | TN | precision | recall | specificity | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 628 | 67 | 69 | 250 | 242 | 0.493 | 0.211 | 0.778 | 0.296 |
| language python | 428 | 45 | 43 | 172 | 168 | 0.511 | 0.207 | 0.796 | 0.295 |
| language ts | 200 | 22 | 26 | 78 | 74 | 0.458 | 0.220 | 0.740 | 0.297 |

Confirmed (test-confirmed only):

| slice | n | TP | FP | FN | TN | precision | recall | specificity | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 628 | 0 | 2 | 317 | 309 | 0.000 | 0.000 | 0.994 | - |
| language python | 428 | 0 | 2 | 217 | 209 | 0.000 | 0.000 | 0.991 | - |
| language ts | 200 | 0 | 0 | 100 | 100 | - | 0.000 | 1.000 | - |

The report's confidence strong slice is broke cases with confidence revert, explicit-merge-fix, or medium, plus every clean case from those same repos. Flagged metrics for the confidence rows are in `docs/bench/report-summary.md`.

### Hand-checked ground truth

49 mined-broke cases were hand-checked (23 pilot + 26 full): 3 interaction, 11 unsure, 35 unrelated.

- Pilot, 23: 1 interaction, 4 unsure, 18 unrelated.
- Full, 26 (all strong revert/medium + 10 random weak, seed 7): 2 interaction, 7 unsure, 17 unrelated.

Hand ground truth is mined clean plus those hand labels. Unsure and unlabeled broke cases are excluded. That leaves n=349 ok cases (346 clean, 3 broke).

Flagged:

| slice | n | TP | FP | FN | TN | precision | recall | specificity | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 349 | 0 | 73 | 3 | 273 | 0.000 | 0.000 | 0.789 | - |
| language python | 238 | 0 | 46 | 3 | 189 | 0.000 | 0.000 | 0.804 | - |
| language ts | 111 | 0 | 27 | 0 | 84 | 0.000 | - | 0.757 | - |

Confirmed (test-confirmed only): TP 0, FP 2, FN 3, TN 344; precision 0.000, recall 0.000, specificity 0.994. Both test-confirmed cases are false positives on this ground truth. The report's top-catches list is empty: no true positives.

### Three hand-confirmed interactions

All three were missed. For each: MergeGuard level, mined confidence, and the report's worst-misses details.

- `psf__black-c26c7728-2918ea3b`: level low, not flagged; mined confidence medium. Verification: `-`. Branch tests: no branch test passed on its own branch, nothing to compare. [A](https://github.com/psf/black/commit/c26c7728e883db1425f7ed7affec41da3b3200a3) [B](https://github.com/psf/black/commit/2918ea3b079bbb617b2f9f0d5bc0b84fde04e48e) [fix](https://github.com/psf/black/commit/9b161072c13c0ec32c9ca9bd48fad17f781a56d4).
- `psf__black-658c8d8d-aafc21aa`: level medium, not flagged; mined confidence medium. Verification: `-`. Branch tests: neither branch added or modified a test file. [A](https://github.com/psf/black/commit/658c8d8d96047c5ba77be4aecc2545a22d5e35b9) [B](https://github.com/psf/black/commit/aafc21aa77f5c4d2ebcb833aa60faba6c2138b94) [fix](https://github.com/psf/black/commit/78163939f157d9e18a8c0528fc5e1c58b1c1e69c).
- `fastapi__fastapi-73c411e1-98244866`: level none, not flagged; mined confidence weak. Verification: `-`. Branch tests: `-`. [A](https://github.com/fastapi/fastapi/commit/73c411e1b92a1659fef76655562e6d2f28be064e) [B](https://github.com/fastapi/fastapi/commit/982448661630e2f412f18a192eed29d0da4a8fb1) [fix](https://github.com/fastapi/fastapi/commit/bba4d4c95e0de8fc9c99f88c269909d37d64d494).

### Textual conflicts

106 of the 136 flagged cases have a textual merge conflict when A and B are replayed together. 112 ok cases have a textual conflict in total; 106 of those 112 are flagged, and 6 are not. 30 flagged cases have no textual conflict (11 mined broke, 19 mined clean).

## Per-repo

Per-repo confusion rows (mined and hand, flagged and confirmed) and the non-ok status table are in [`docs/bench/report-summary.md`](report-summary.md). They are not copied here. The report also lists hand-ground-truth false alarms case by case.

## Reliability

replay_failed is 32/660 = 4.8%. By repo: pallets__werkzeug 10, marshmallow-code__marshmallow 9, PyCQA__isort 3, aio-libs__multidict 2, pallets__flask 2, tqdm__tqdm 2, pallets__click 1, pallets__jinja 1, psf__black 1, python-jsonschema__jsonschema 1. Timeout 0. Error 0 (error rate 0%).

Both disk incidents were during the full run, 2026-10-22 CEST. The tables above are the run after the fixes.

1. ~00:50: ENOSPC from per-case replay `.venv` dirs (~12G), then mass replay_failed. Runs were aborted. Results were moved to `full-aborted-enospc` / `full-b-aborted-enospc` and kept only for spend. Fix `1a772d4`: delete each replay venv after its case, and stop on `--min-free-gb`.
2. ~01:22: MergeGuard JS dep cache `backend/.workspace/js-deps` grew to 24G (37 lockfile-keyed installs, ~1.3G each, mostly zod). ENOSPC surfaced as a git `RuntimeError`, so it was missed. Records containing "No space left" were moved to `full2-*/dropped-enospc.jsonl` (187 records). Fix `4ed2c6e`: per-case workspace (js-deps inside the replay dir, deleted after the case); any ENOSPC text is a disk stop, and no record is written.

After the fixes, footprint stayed ~2-4G. The final run reported free disk ~42G. Runners for the final records ran in parallel from 01:09 to ~03:00 CEST.

## Cost

Total spend is the sum of `cost` over all result records, including pilot, aborted, and dropped: $4.9031 of the $10 cap.

| records | spend |
|---|---:|
| final full2-a..j | $4.7019 |
| pilot | $0.0486 |
| full-aborted-enospc | $0.0871 |
| full-b-aborted-enospc | $0.0485 |
| dropped-enospc (full2) | $0.0169 |
| dry-pilot | $0 |

The report prints the final full2-a..j total as 4.702 (mean 0.007, median 0.001 per case). Case time on those final records is 20704.124 seconds (mean 31.370, median 8.713).

Not included: LLM calls in flight when runners were killed for splitting (no record written), and Grok Build coding sessions. Analyses use a cache, so re-analysed cases show cost 0.

## Caveats

- Mined follow-up-fix positives are mostly noise: 3 of 49 hand-checked cases are real A x B interactions, 35 are unrelated, 11 are unsure. Mined precision and recall do not measure real interaction detection.
- The hand sample is tiny (3 positives). Recall on hand ground truth is not statistically meaningful.
- Verification ("confirmed") rarely runs. TypeScript cases show "no Python changes". Several zod cases show "yarn not found".
- Clean pairs are mined (merged with no follow-up fix), not hand-checked. A flagged textual-conflict clean pair counts as a false positive.
- Single run, no repeat. LLM nondeterminism is not measured.
