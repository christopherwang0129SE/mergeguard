# MergeGuard Demo Repo

A tiny git repository with a **semantic merge conflict that git cannot see**.
It is used by the dashboard's **Try demo repo** button and by the demo video.

## What it contains

| Branch | Change |
| --- | --- |
| `main` | `checkout.py` (price × qty, discount, tax) + `python3 -m unittest` tests |
| `feat/discount-cap` | Caps the discount at a fixed amount (`MAX_DISCOUNT = 20.00`), applied to the **pre-tax subtotal**. Touches only `apply_discount` (top of the file). |
| `feat/tax-before-discount` | Applies tax **before** the discount in `total()` (bottom of the file). |
| `docs/readme` | Harmless branch that only edits `README.md`. |

Both feature branches change the **same file** (`checkout.py`) but in far-apart
regions (more than the 3-line adjacency window the Sentinel uses). So:

- **git heuristic**: 1 shared file, 0 overlapping regions → low risk (~10/100).
- **Nemotron**: sees both changes in the shared-file diff excerpt → flags the
  semantic clash (the discount cap assumes a pre-tax subtotal, while the other
  branch taxes before discounting).

Each branch passes its tests on its own. After merging both branches (no
textual conflict) the result is wrong and at least one test fails.

## Recreate the demo

The repo is generated from scratch (idempotent — the old folder is removed
first) and lives in `demo/checkout-demo/` (git-ignored, never committed):

```bash
python3 demo/make_demo_repo.py
```

## Verify the merge failure

`--verify` clones the demo repo into a temporary directory, merges both
feature branches into a scratch branch, runs the tests, and prints the failing
assert. The scratch branch only exists in the temp clone — `demo/checkout-demo/`
keeps exactly `main`, `feat/discount-cap`, `feat/tax-before-discount`, and
`docs/readme` (no scratch branches, so they never enter the analysis):

```bash
python3 demo/make_demo_repo.py --verify
```

Expected output (abridged):

```
Merged feat/discount-cap and feat/tax-before-discount into scratch/merge-both
Running tests on the merged result...
...
FAIL: test_total_with_cap (test_discount_cap.DiscountCapTests.test_total_with_cap)
AssertionError: 200.0 != 198.0
...
FAILED (failures=2)
Expected failure: the merged result is semantically wrong.
```

## Using it in the dashboard

The dashboard's **Try demo repo** button fills the repository field with the
absolute path to `demo/checkout-demo/`. The path is computed in
`frontend/vite.config.ts` (`define`, from the config's location) and can be
overridden with the `VITE_DEMO_REPO_PATH` environment variable. See
`frontend/README.md`.
