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
- **Branch Risk Lights**: Shows a colored light for each branch indicating the highest risk level among pairs involving that branch.
- **Pair Table**: Lists all branch pairs sorted by risk score (descending), showing score, level, score source, rationale, and fallback reason if applicable.
- **Agent Status**: Displays the status of the Sentinel agent (based on Nebius readiness) and notes that Planner and Merger are planned (not implemented).
- **Loading and Error States**: Handles loading, empty responses, and errors gracefully.

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
- `src/services/api.ts`: Service layer for communicating with the backend.
- `src/types.ts`: TypeScript interfaces for the API responses.
- `src/utils/risk.ts`: Utility functions for mapping risk levels to colors and text, computing branch risk levels, and sorting pairs.
- `src/components/`: Contains `BranchLights`, `PairTable`, and `AgentStatus` components.
- `src/utils/risk.test.ts`: Unit tests for the risk utilities.

## Acknowledgements

- UI references were sought via the Uizze tool (no specific references found for this dashboard).
- No Paper file was open during development, so no mockup was created.
