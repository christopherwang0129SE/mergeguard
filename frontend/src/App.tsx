import { useState, useEffect, useCallback } from 'react';
import type { AnalyzeRequest, AnalyzeResponse, HealthResponse } from './types';
import { analyzeRepo, checkHealth } from './services/api';
import { classifyRepoInput } from './utils/repo';
import { BranchLights } from './components/BranchLights';
import { PairTable } from './components/PairTable';
import { AgentStatus } from './components/AgentStatus';
import './App.css';

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
    // Fetch health on mount so the agent panel reflects /api/health before any
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

  return (
    <>
      <section id="dashboard">
        <div className="container">
          <h1>MergeGuard Dashboard</h1>
          <form onSubmit={handleSubmit} className="input-form">
            <div>
              <label>
                Repository path or URL:
                <input
                  type="text"
                  placeholder="/path/to/repo or https://github.com/org/repo.git"
                  value={repoInput}
                  onChange={(e) => setRepoInput(e.target.value)}
                />
              </label>
            </div>
            <div>
              <label>
                Base Branch:
                <input
                  type="text"
                  placeholder="main"
                  value={baseBranch}
                  onChange={(e) => setBaseBranch(e.target.value)}
                />
              </label>
            </div>
            <button type="submit" disabled={loading}>
              {loading ? 'Analyzing...' : 'Analyze'}
            </button>
          </form>

          {error && <div className="error">{error}</div>}

          {loading && !response && <div className="loading">Loading...</div>}

          {response && (
            <>
              <BranchLights branches={response.branches} pairs={response.pairs} />
              <PairTable pairs={response.pairs} risk_model={response.risk_model ?? null} />
            </>
          )}

          {/* Agent panel is driven by /api/health and shows before any analyze. */}
          <AgentStatus health={health} />
        </div>
      </section>
    </>
  );
}

export default App;
