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
- **Branch Risk Lights**: Shows a colored light for each branch indicating the highest risk level among pairs involving that branch (via `getBranchRiskLevel`).
- **Pair Table**: Lists all branch pairs sorted by risk score (descending), showing the published `score`, `level`, `git_score` and `model_score` side by side, a `score_source` badge (`model` / `git`), a `fallback_reason` badge when present, and the `rationale`. Shows `risk_model` when set.
- **Agent Status**: Driven by `GET /api/health` and shown before any analyze. Sentinel is **online** whenever health responds (git scoring works without Nebius) and **offline** when it does not. The mode is shown as **git only** or **git+Nemotron** (from `nebius_ready` / `nebius_enabled`, plus `nebius_model`). Planner and Merger are honestly shown as **planned**.
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
