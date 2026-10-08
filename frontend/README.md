# MergeGuard Dashboard (Frontend)

This is the frontend for the MergeGuard dashboard MVP (T1). It provides a UI to visualize branch risk lights, pair risks, and agent status.

## Getting Started

### Prerequisites

- Node.js (v18 or later)
- npm (comes with Node.js)
- The MergeGuard backend running (see below)

### Installation

1. Clone the repository (if you haven't already) and navigate to the frontend directory:
   ```bash
   cd /path/to/mergeguard/frontend
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

### Configuration

The frontend expects the backend to be running at `http://localhost:8000` by default. You can change this by setting the `VITE_API_BASE_URL` environment variable (via a `.env` file or directly).

Create a `.env` file in the frontend directory (if needed) with:
   ```
   VITE_API_BASE_URL=http://localhost:8000
   ```

#### Demo repo path (`VITE_DEMO_REPO_PATH`)

The **Try demo repo** button fills the repository field with the path to the
demo repository (`demo/checkout-demo/`, see `demo/README.md`). The path is
computed in `vite.config.ts` via `define` from the config's location, so it
works from any checkout. To point the button at a different location, set
`VITE_DEMO_REPO_PATH` in the environment (it takes precedence over the
computed default):

```bash
VITE_DEMO_REPO_PATH=/some/other/path/to/checkout-demo npm run dev
```

Recreate the demo repo with `python3 demo/make_demo_repo.py` (idempotent).

### Running the Backend

From the root of the repository, start the backend server:
   ```bash
   cd backend
   uvicorn app.main:app --reload --port 8000
   ```

### Running the Frontend

In another terminal, from the frontend directory:
   ```bash
   npm run dev
   ```

The dashboard will be available at `http://localhost:3000`.

### Building for Production

To build the frontend for production:
   ```bash
   npm run build
   ```

The built assets will be in the `dist` directory.

### Running Tests

To run the unit tests:
   ```bash
   npm test
   ```

## Features

- **Form Input**: Enter a repository path or URL and a base branch (defaults to `main`) to analyze.
  - Remote Git URLs (`https://…`, `git@…`, `ssh://…`) are sent as `repo_url`.
  - Local paths (`/…`, `./…`, `~/…`) are sent as `repo_path`.
  - The two fields are mutually exclusive — only one is ever sent.
  - **Try demo repo** fills the field with the demo repo path (`VITE_DEMO_REPO_PATH`, default `demo/checkout-demo/`).
- **Summary Bar**: After an analyze, a card under the form shows the worst *effective* level across pairs and how many pairs are risky. A pair is **risky** when its published level is medium/high **or** it is a hidden conflict (the model flagged a medium/high semantic clash the git heuristic missed). Headline rules:
  - only hidden conflicts, nothing published medium/high → `1 hidden conflict among 3 pairs` (plural: `2 hidden conflicts among …`);
  - shown medium/high pairs → `N of M branch pairs at <worst> risk`, with `· C confirmed by test` appended when verify-by-test confirmed C pairs and `· K hidden` when hidden conflicts also exist (e.g. `1 of 3 branch pairs at high risk · 1 confirmed by test · 1 hidden`);
  - a **confirmed by test** pair is published by the backend as level **high** with a shown score of at least 90; the Level cell adds `raised: confirmed by test (blend N)` while the Git Score and Model Score columns keep their original values. A suspected verdict keeps the blended score.
  - nothing risky → `No risky pairs among M`.
  The card's left border and the branch lights use the worst *effective* level (amber for medium, red for high). Line 2 reports Nemotron agreement: `Nemotron disagrees on N pair(s)` / `Nemotron agrees with git on all pairs` / `Git only: Nemotron unavailable`. A pair counts as a disagreement when `score_source === 'model'`, the model level differs from the git level, and at least one side is medium or high — a none ↔ low move is not a disagreement.
- **Branch Risk Lights**: Shows a colored light for each branch indicating the highest *effective* risk level among pairs involving that branch (via `getBranchRiskLevel`). The effective level of a pair is its published level, raised to the model's level when the pair is a hidden conflict — so a branch with a hidden high conflict shows a red light even if its published score is low.
- **Pair Table**: Lists all branch pairs sorted by risk score (descending). The published risk is shown in the Level column as `Risk N / 100` plus the level word. `git_score` and `model_score` are shown side by side. The source badge is `Nemotron` or `Git fallback` (with the `fallback_reason` appended, e.g. `Git fallback · no_api_key`).
  - **Floor rule**: when `overlapping_files > 0` and `git_score > model_score`, a `Floor rule: N shared files` chip is shown and the rationale is two lines: the model's assessment plus `Floor rule: both branches touch <files>, so kept <Level>.` (file names from the overlapping `collisions`).
  - **Hidden conflict**: when `score_source === 'model'`, the model level is **medium or high**, and it is higher than the git level (same thresholds as the backend: `>=70` high, `>=40` medium, `>0` low, else none), a `Nemotron flags hidden conflict` chip is shown, the full model rationale is displayed, and the row is highlighted. A none → low move is not a hidden conflict: the model still sees no real risk.
- **Agent Status**: A compact strip in the header, driven by `GET /api/health` and shown before any analyze. Sentinel is **online** whenever health responds (git scoring works without Nebius) and **offline** when it does not. The mode is shown as **git only** or **git+Nemotron** (from `nebius_ready` / `nebius_enabled`, plus `nebius_model` once, in mono). Planner and Merger are honestly shown as **soon** chips.
- **Loading and Error States**: Handles loading, empty responses, and errors gracefully. Non-JSON error bodies (e.g. a gateway `502`) surface the status and text.

## API

All requests go to the backend under the `/api` prefix:

- `POST {base}/api/analyze`
- `GET {base}/api/health`

The base URL is configurable via `VITE_API_BASE_URL` (default `http://localhost:8000`).

## Design Notes

- The color mapping for risk levels is:
  - None/Low: Green (`#10b981`)
  - Medium: Amber (`#f59e0b`)
  - High: Red (`#ef4444`)
- The level is also displayed as text for accessibility.
- **Git level thresholds** (mirrored from the backend, `backend/app/services/sentinel.py` `_level`): a git score of `>=70` is **high**, `>=40` is **medium**, `>0` is **low**, otherwise **none**. Used by the hidden-conflict detection (`isHiddenConflict`) to compare the model level against the git level.
- **Hidden conflict vs disagreement**: `isHiddenConflict` requires `score_source === 'model'`, a model level of medium or high, and a model level above the git level. `isDisagree` (summary bar) requires `score_source === 'model'`, differing levels, and at least one side medium or high — so a none ↔ low move is neither a hidden conflict nor a disagreement.
- **Effective level** (`effectivePairLevel`): the published level, raised to the model's level for hidden conflicts. The summary bar headline, the card's left border color, and the branch lights all use the effective level, so a hidden medium/high conflict is never hidden behind a low published score. A pair is "risky" for the headline when its effective level is medium or high.
- The dashboard uses minimal dependencies: React, Vite, and TypeScript.

## Development

This project was bootstrapped with [Vite](https://vitejs.dev/) using the React + TypeScript template.

### Code Structure

- `src/App.tsx`: Main application component.
- `src/services/api.ts`: Service layer for communicating with the backend (`/api/analyze`, `/api/health`).
- `src/types.ts`: TypeScript interfaces for the API requests/responses.
- `src/utils/risk.ts`: Utility functions for mapping risk levels to colors and text, computing branch risk levels, and sorting pairs.
- `src/utils/repo.ts`: Classifies a repository input into `repo_url` or `repo_path`.
- `src/utils/agent.ts`: Derives Sentinel status (online/offline) and mode (git only / git+Nemotron) from health.
- `src/components/`: Contains `BranchLights`, `PairTable`, and `AgentStatus` components.
- `src/utils/risk.test.ts`, `src/utils/repo.test.ts`, `src/utils/agent.test.ts`, `src/services/api.test.ts`, and `src/components/*.test.tsx`: Unit and render tests.

## Acknowledgements

- UI references were sought via the Uizze tool (no specific references found for this dashboard).
- No Paper file was open during development, so no mockup was created.
