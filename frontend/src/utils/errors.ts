export interface ErrorHelp {
  title: string;
  body: string;
  /** What to do next, with an optional shell command to run. */
  fix?: { text: string; command?: string };
}

export const BACKEND_START_COMMAND = 'cd backend && uvicorn app.main:app --port 8000';
export const DEMO_REPO_COMMAND = 'python3 demo/make_demo_repo.py';
export const API_DEMO_REPO_COMMAND = 'python3 demo/make_api_demo_repo.py';

/**
 * Turn a failed /api/analyze call into a title, the cause and the next step.
 * The backend returns FastAPI `detail` strings; a network failure surfaces
 * as the browser's fetch TypeError message.
 */
export const describeError = (message: string, apiBase: string): ErrorHelp => {
  if (/failed to fetch|networkerror|load failed|network request failed/i.test(message)) {
    return {
      title: "Can't reach the MergeGuard backend",
      body: `Nothing answered at ${apiBase}.`,
      fix: { text: 'Start the backend from the repository root, then try again:', command: BACKEND_START_COMMAND },
    };
  }
  if (/not a git repository/i.test(message)) {
    return {
      title: "That path isn't a git repository",
      body: message,
      fix: {
        text: 'Check the path. To use a bundled demo instead, create it from the repository root and choose the matching button:',
        command: `${DEMO_REPO_COMMAND}\n${API_DEMO_REPO_COMMAND}`,
      },
    };
  }
  if (/no branches to analyze/i.test(message)) {
    return {
      title: 'No branches to compare',
      body: message,
      fix: { text: 'Check the base branch name, or push at least two branches besides it.' },
    };
  }
  if (/^demo mode/i.test(message)) {
    return {
      title: 'This deployment only runs the bundled demos',
      body: message,
      fix: { text: 'Choose Try demo repo or Try API demo (Tavily), or run MergeGuard locally to analyze your own repository.' },
    };
  }
  if (/clone failed/i.test(message)) {
    return {
      title: "Couldn't clone that repository",
      body: message,
      fix: {
        text: 'Check the URL and that the repository is public. GitHub repo and pull request URLs both work, for example https://github.com/owner/repo/pull/12.',
      },
    };
  }
  return { title: 'Analysis failed', body: message };
};
