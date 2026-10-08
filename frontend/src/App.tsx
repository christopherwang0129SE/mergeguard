import { useState, useEffect } from 'react';
import type { AnalyzeRequest } from './types';
import { analyzeRepo, checkHealth } from './services/api';
import { BranchLights } from './components/BranchLights';
import { PairTable } from './components/PairTable';
import { AgentStatus } from './components/AgentStatus';
import './App.css';

function App() {
  const [request, setRequest] = useState<AnalyzeRequest>({
    base_branch: 'main',
  });
  const [response, setResponse] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<any>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await analyzeRepo(request);
      setResponse(res);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
  }, []);

  const fetchHealth = async () => {
    try {
      const res = await checkHealth();
      setHealth(res);
    } catch (err) {
      console.error('Failed to fetch health', err);
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
                Repository Path or URL:
                <input
                  type="text"
                  placeholder="Enter local repo path or Git URL"
                  value={request.repo_path ?? request.repo_url ?? ''}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                    const value = e.target.value.trim();
                    setRequest((prev) => ({
                      ...prev,
                      repo_path: value ? value : undefined,
                      repo_url: value && !value.startsWith('/') && !value.includes(':') ? value : undefined, // simple heuristic
                    }));
                  }}
                />
              </label>
            </div>
            <div>
              <label>
                Base Branch:
                <input
                  type="text"
                  placeholder="main"
                  value={request.base_branch ?? ''}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                    setRequest((prev) => ({ ...prev, base_branch: e.target.value || 'main' }));
                  }}
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
              <PairTable pairs={response.pairs} risk_model={response.risk_model} />
              <AgentStatus health={health} />
            </>
          )}
        </div>
      </section>
    </>
  );
}

export default App;