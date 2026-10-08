import { useState, useEffect, useCallback } from 'react';
import type { AnalyzeRequest, AnalyzeResponse, HealthResponse } from './types';
import { analyzeRepo, checkHealth } from './services/api';
import { classifyRepoInput } from './utils/repo';
import { BranchLights } from './components/BranchLights';
import { PairTable } from './components/PairTable';
import { SummaryBar } from './components/SummaryBar';
import { AgentStatus } from './components/AgentStatus';
import './App.css';

// Demo repo path, injected by Vite (see vite.config.ts `define`).
const DEMO_REPO_PATH: string = import.meta.env.VITE_DEMO_REPO_PATH ?? '';

function App() {
  const [repoInput, setRepoInput] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');
  const [response, setResponse] = useState<AnalyzeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);

  const fetchHealth = useCallback(async () => {
    try {
      const res = await checkHealth();
      setHealth(res);
    } catch (err) {
      // Health unavailable -> Sentinel shows offline. Keep the panel visible.
      setHealth(null);
      console.error('Failed to fetch health', err);
    }
  }, []);

  useEffect(() => {
    // Fetch health on mount so the agent strip reflects /api/health before any
    // analyze. fetchHealth is async, so setHealth runs after the await, not
    // synchronously in the effect.
    // eslint-disable-next-line react/set-state-in-effect
    fetchHealth();
  }, [fetchHealth]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const repoField = classifyRepoInput(repoInput);
    if (!repoField.repo_url && !repoField.repo_path) {
      setError('Enter a repository path or URL.');
      return;
    }
    const request: AnalyzeRequest = {
      ...repoField,
      base_branch: baseBranch || 'main',
    };
    setLoading(true);
    setError(null);
    try {
      const res = await analyzeRepo(request);
      setResponse(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  const handleTryDemo = () => {
    if (DEMO_REPO_PATH) {
      setRepoInput(DEMO_REPO_PATH);
      setError(null);
    }
  };

  return (
    <div id="dashboard">
      <header className="app-header">
        <div className="container">
          <div className="header-row">
            <div>
              <h1>MergeGuard</h1>
              <p className="tagline">
                Predicts merge conflicts between parallel branches before you merge.
              </p>
            </div>
            <AgentStatus health={health} />
          </div>
        </div>
      </header>

      <main className="container">
        <form onSubmit={handleSubmit} className="input-form" noValidate>
          <div className="field field-repo">
            <label htmlFor="repo-input">Repository path or URL</label>
            <input
              id="repo-input"
              type="text"
              placeholder="/path/to/repo or https://github.com/org/repo.git"
              value={repoInput}
              onChange={(e) => setRepoInput(e.target.value)}
            />
          </div>
          <div className="field field-base">
            <label htmlFor="base-input">Base branch</label>
            <input
              id="base-input"
              type="text"
              placeholder="main"
              value={baseBranch}
              onChange={(e) => setBaseBranch(e.target.value)}
            />
          </div>
          <button type="button" className="btn-secondary" onClick={handleTryDemo}>
            Try demo repo
          </button>
          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? 'Analyzing...' : 'Analyze'}
          </button>
        </form>
        <p className="form-hint">
          Point at a repo. Sentinel diffs every branch against main. Nemotron explains each risky pair.
        </p>

        {error && <div className="error">{error}</div>}

        {loading && !response && <div className="loading">Loading...</div>}

        {response && (
          <>
            <SummaryBar pairs={response.pairs} usage={response.usage ?? null} />
            <BranchLights branches={response.branches} pairs={response.pairs} />
            <PairTable pairs={response.pairs} risk_model={response.risk_model ?? null} />
          </>
        )}
      </main>
    </div>
  );
}

export default App;
