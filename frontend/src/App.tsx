import { useState, useEffect, useCallback, useRef } from 'react';
import type { AnalyzeRequest, AnalyzeResponse, HealthResponse, PairRisk } from './types';
import { analyzeRepo, checkHealth, API_BASE_URL } from './services/api';
import { analyzeStream, StreamUnavailableError } from './services/stream';
import { classifyRepoInput } from './utils/repo';
import { describeError, BACKEND_START_COMMAND } from './utils/errors';
import { rankPairs, summaryLine } from './utils/verdict';
import { activeStage, finishRun, formatClock, initialRun, pairKey, reduceRun } from './utils/run';
import type { RunState } from './utils/run';
import { formatHash, parseHash } from './utils/hash';
import { AgentStatus } from './components/AgentStatus';
import { Pipeline } from './components/Pipeline';
import { PairIndex } from './components/PairIndex';
import { PairDetail } from './components/PairDetail';
import { RunSummary } from './components/RunSummary';
import { ErrorPanel } from './components/ErrorPanel';
import './App.css';

// Demo repo paths, injected by Vite (see vite.config.ts `define`).
// An empty string hides that button.
const DEMO_REPO_PATH: string = import.meta.env.VITE_DEMO_REPO_PATH ?? '';
const API_DEMO_REPO_PATH: string = import.meta.env.VITE_API_DEMO_REPO_PATH ?? '';

type Phase = 'idle' | 'running' | 'done' | 'error' | 'cancelled';

interface Target {
  repo: string;
  base: string;
  source: Pick<AnalyzeRequest, 'repo_path' | 'repo_url'>;
}

const isAbort = (err: unknown): boolean =>
  (err instanceof DOMException && err.name === 'AbortError') || (err instanceof Error && err.name === 'AbortError');

/** Below this width the pair list and the detail stack. */
const STACKED = '(max-width: 959px)';

function App() {
  const [repoInput, setRepoInput] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthChecked, setHealthChecked] = useState(false);

  const [phase, setPhase] = useState<Phase>('idle');
  const [streamed, setStreamed] = useState(true);
  const [run, setRun] = useState<RunState>(initialRun);
  const [response, setResponse] = useState<AnalyzeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [target, setTarget] = useState<Target | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');

  const repoRef = useRef<HTMLInputElement>(null);
  const summaryRef = useRef<HTMLHeadingElement>(null);
  const errorRef = useRef<HTMLHeadingElement>(null);
  const detailRef = useRef<HTMLHeadingElement>(null);
  const pipelineRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // A pair named in the URL, selected once the run has scored it.
  const wantedPair = useRef<string | null>(null);
  // Bumped per request so a slow, superseded response is ignored.
  const requestId = useRef(0);

  const loading = phase === 'running';
  const selectedRef = useRef<string | null>(null);
  useEffect(() => {
    selectedRef.current = selectedKey;
  }, [selectedKey]);

  const fetchHealth = useCallback(async () => {
    try {
      setHealth(await checkHealth());
    } catch {
      setHealth(null);
    } finally {
      setHealthChecked(true);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect
    fetchHealth();
  }, [fetchHealth]);

  // Elapsed clock while an analysis runs.
  useEffect(() => {
    if (!loading) return;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed((Date.now() - started) / 1000), 250);
    return () => window.clearInterval(timer);
  }, [loading]);

  // Announce stage changes, not every tick of the clock.
  const stageTitle = loading ? activeStage(run)?.title : undefined;
  useEffect(() => {
    if (stageTitle) {
      // eslint-disable-next-line react/set-state-in-effect
      setAnnouncement(`${stageTitle} running.`);
    }
  }, [stageTitle]);

  // Move focus to the outcome so keyboard and screen-reader users land on it.
  useEffect(() => {
    if (phase === 'done') summaryRef.current?.focus({ preventScroll: true });
    if (phase === 'error') errorRef.current?.focus();
  }, [phase]);

  const writeHash = (t: Target | null, key: string | null) => {
    if (!t) return;
    const [a, b] = key ? key.split('..') : [];
    const hash = formatHash({ repo: t.repo, base: t.base, pair: a && b ? { a, b } : undefined });
    if (window.location.hash !== hash) {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${hash}`);
    }
  };

  const runAnalysis = async (repo: string, base: string) => {
    const repoField = classifyRepoInput(repo);
    if (!repoField.repo_url && !repoField.repo_path) {
      setFieldError('Enter a repository path or URL, or choose a demo.');
      repoRef.current?.focus();
      return;
    }
    const baseName = base.trim() || 'main';
    const request: AnalyzeRequest = { ...repoField, base_branch: baseName };
    const id = ++requestId.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const nextTarget: Target = { repo: repo.trim(), base: baseName, source: repoField };
    setTarget(nextTarget);
    setFieldError(null);
    setError(null);
    setResponse(null);
    setRun(initialRun());
    setStreamed(true);
    setElapsed(0);
    setPhase('running');
    setAnnouncement('Analysis started.');
    if (!wantedPair.current) setSelectedKey(null);
    writeHash(nextTarget, wantedPair.current);

    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    requestAnimationFrame(() =>
      pipelineRef.current?.scrollIntoView?.({ block: 'nearest', behavior: reduce ? 'auto' : 'smooth' })
    );

    const onEvent = (event: Parameters<typeof reduceRun>[1]) => {
      if (id !== requestId.current) return;
      setRun((r) => reduceRun(r, event));
      if (event.type === 'pair') {
        // First scored pair, or the one the URL asked for: open it.
        const key = pairKey(event.pair);
        setSelectedKey((current) => {
          if (wantedPair.current) return wantedPair.current === key ? key : current;
          return current ?? key;
        });
      }
    };

    try {
      let result: AnalyzeResponse;
      try {
        result = await analyzeStream(request, onEvent, controller.signal);
      } catch (err) {
        if (!(err instanceof StreamUnavailableError)) throw err;
        if (id !== requestId.current) return;
        setStreamed(false);
        result = await analyzeRepo(request, controller.signal);
      }
      if (id !== requestId.current) return;
      setResponse(result);
      setRun((r) => finishRun(r, result));
      const ranked = rankPairs(result.pairs);
      const keys = ranked.map(pairKey);
      const wanted = wantedPair.current;
      const current = selectedRef.current;
      wantedPair.current = null;
      const chosen =
        wanted && keys.includes(wanted) ? wanted : current && keys.includes(current) ? current : keys[0] ?? null;
      setSelectedKey(chosen);
      writeHash(nextTarget, chosen);
      setPhase('done');
      setAnnouncement(`Analysis finished. ${summaryLine(result.pairs)}`);
    } catch (err) {
      if (id !== requestId.current) return;
      if (isAbort(err)) {
        setPhase('cancelled');
        setAnnouncement('Analysis cancelled.');
        return;
      }
      const message = err instanceof Error ? err.message : 'Unknown error';
      setError(message);
      setPhase('error');
      setAnnouncement(`Analysis failed. ${describeError(message, API_BASE_URL).title}.`);
      // A failed analyze may mean the backend went down: refresh the header.
      fetchHealth();
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const cancel = () => abortRef.current?.abort();

  // A shared link (#repo=...&pair=a..b) re-runs that analysis and opens the pair.
  useEffect(() => {
    const state = parseHash(window.location.hash);
    if (!state.repo) return;
    wantedPair.current = state.pair ? `${state.pair.a}..${state.pair.b}` : null;
    /* eslint-disable react/set-state-in-effect */
    setRepoInput(state.repo);
    if (state.base) setBaseBranch(state.base);
    /* eslint-enable react/set-state-in-effect */
    runAnalysis(state.repo, state.base ?? 'main');
    return () => abortRef.current?.abort();
    // Mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Editing the hash by hand (or back/forward) switches the open pair.
  useEffect(() => {
    const onHash = () => {
      const state = parseHash(window.location.hash);
      if (state.pair) setSelectedKey(`${state.pair.a}..${state.pair.b}`);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const selectPair = (pair: PairRisk) => {
    const key = pairKey(pair);
    setSelectedKey(key);
    writeHash(target, key);
    // Stacked layout: the detail sits below the list, take the reader there.
    if (window.matchMedia?.(STACKED).matches) {
      requestAnimationFrame(() => {
        const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        detailRef.current?.scrollIntoView?.({ block: 'start', behavior: reduce ? 'auto' : 'smooth' });
        detailRef.current?.focus({ preventScroll: true });
      });
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    wantedPair.current = null;
    runAnalysis(repoInput, baseBranch);
  };

  const handleTryDemo = (path: string) => {
    if (!path) return;
    wantedPair.current = null;
    setRepoInput(path);
    setBaseBranch('main');
    runAnalysis(path, 'main');
  };

  const backendOffline = healthChecked && !health;
  const errorHelp = error ? describeError(error, API_BASE_URL) : null;
  const pairs = response?.pairs ?? run.pairs;
  const selected = pairs.find((p) => pairKey(p) === selectedKey) ?? null;
  const pending = loading ? Math.max(0, (run.branches.length * (run.branches.length - 1)) / 2 - run.pairs.length) : 0;
  const showWorkspace = target && (response || (loading && (run.pairs.length > 0 || run.branches.length > 1)));
  const pipelineMode = phase === 'idle' ? 'idle' : loading ? (streamed ? 'live' : 'blind') : 'finished';
  const showPipeline = phase === 'idle' || loading || (streamed && phase !== 'error');

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="wordmark" href="/" aria-label="MergeGuard home">
            <span className="wordmark-glyph" aria-hidden="true">
              <span></span>
              <span></span>
            </span>
            MergeGuard
          </a>
          <AgentStatus health={health} checked={healthChecked} />
        </div>
      </header>

      <main className="page" id="main" tabIndex={-1}>
        <section className="intake" data-phase={phase} aria-labelledby="intake-heading">
          <h1 id="intake-heading" className={phase === 'idle' ? 'intake-title' : 'intake-title is-compact'}>
            Find branch pairs that merge cleanly and still break
          </h1>
          {phase === 'idle' && (
            <p className="intake-lede">
              MergeGuard diffs every branch against the base, asks Nemotron which pairs interact, and proves a
              conflict by running tests on the merged code.
            </p>
          )}

          <form onSubmit={handleSubmit} className="repo-form" noValidate aria-label="Analyze a repository">
            <div className="field field-repo">
              <label htmlFor="repo-input">Repository</label>
              <input
                id="repo-input"
                ref={repoRef}
                type="text"
                name="repo"
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                placeholder="/path/to/repo or https://github.com/owner/repo"
                value={repoInput}
                aria-invalid={fieldError ? true : undefined}
                aria-describedby={fieldError ? 'repo-hint repo-error' : 'repo-hint'}
                onChange={(e) => {
                  setRepoInput(e.target.value);
                  if (fieldError) setFieldError(null);
                }}
              />
              <p className="field-hint" id="repo-hint">
                Local path, git URL, GitHub repository or pull request URL.
              </p>
              {fieldError && (
                <p className="field-error" id="repo-error">
                  {fieldError}
                </p>
              )}
            </div>
            <div className="field field-base">
              <label htmlFor="base-input">Base branch</label>
              <input
                id="base-input"
                type="text"
                name="base-branch"
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                placeholder="main"
                value={baseBranch}
                onChange={(e) => setBaseBranch(e.target.value)}
              />
            </div>
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? 'Analyzing…' : 'Analyze'}
            </button>
          </form>

          {(DEMO_REPO_PATH || API_DEMO_REPO_PATH) && (
            <div className="demos" role="group" aria-label="Bundled demos">
              {DEMO_REPO_PATH && (
                <div className="demo">
                  <button
                    type="button"
                    className="btn btn-demo"
                    onClick={() => handleTryDemo(DEMO_REPO_PATH)}
                    disabled={loading}
                    aria-describedby={phase === 'idle' ? 'demo-note' : undefined}
                  >
                    Try demo repo
                  </button>
                  {phase === 'idle' && (
                    <p className="demo-note" id="demo-note">
                      Checkout service. Two branches change how discount and tax combine; git merges them cleanly.
                    </p>
                  )}
                </div>
              )}
              {API_DEMO_REPO_PATH && (
                <div className="demo">
                  <button
                    type="button"
                    className="btn"
                    onClick={() => handleTryDemo(API_DEMO_REPO_PATH)}
                    disabled={loading}
                    aria-describedby={phase === 'idle' ? 'api-demo-note' : undefined}
                  >
                    Try API demo (Tavily)
                  </button>
                  {phase === 'idle' && (
                    <p className="demo-note" id="api-demo-note">
                      Payments client on requests and pydantic. Tavily looks up the library docs Nemotron reads.
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

          {backendOffline && phase !== 'error' && (
            <div className="inline-problem">
              <p>
                <strong>The backend isn&apos;t answering</strong> at <code>{API_BASE_URL}</code>. Start it from the
                repository root:
              </p>
              <pre className="command" role="region" tabIndex={0} aria-label="Command to start the backend">
                <code>{BACKEND_START_COMMAND}</code>
              </pre>
            </div>
          )}
        </section>

        <p className="sr-only" role="status" aria-live="polite">
          {announcement}
        </p>

        {phase === 'done' && response && target && (
          <RunSummary response={response} target={target.repo} headingRef={summaryRef} />
        )}

        <div ref={pipelineRef} className="pipeline-slot">
          {showPipeline && (
            <Pipeline
              run={run}
              mode={pipelineMode}
              elapsedS={elapsed}
              onCancel={loading ? cancel : undefined}
            />
          )}
        </div>

        {phase === 'cancelled' && (
          <section className="notice" aria-labelledby="cancel-heading">
            <h2 id="cancel-heading">Analysis cancelled after {formatClock(elapsed)}</h2>
            <p>
              The backend may finish the current step on its own; nothing was saved. Run it again with Analyze or a
              demo button.
            </p>
          </section>
        )}

        {errorHelp && phase === 'error' && (
          <ErrorPanel ref={errorRef} help={errorHelp} onRetry={() => runAnalysis(target?.repo ?? repoInput, target?.base ?? baseBranch)} />
        )}

        {showWorkspace && target && (
          <div className="workspace" data-live={loading || undefined}>
            <PairIndex
              pairs={pairs}
              selectedKey={selectedKey}
              onSelect={selectPair}
              live={loading}
              pendingCount={pending}
            />
            <div className="workspace-detail">
              {selected ? (
                <PairDetail
                  key={pairKey(selected)}
                  pair={selected}
                  source={target.source}
                  baseBranch={response?.base_branch ?? run.baseBranch ?? target.base}
                  usage={response?.usage ?? null}
                  provisional={loading}
                  headingRef={detailRef}
                />
              ) : (
                <p className="detail-empty">
                  {loading ? 'Pairs appear here as Nemotron scores them.' : 'Choose a pair to see the evidence.'}
                </p>
              )}
            </div>
          </div>
        )}
      </main>

      <footer className="footer">
        <p>
          Risk explanations and intent tests by NVIDIA Nemotron 3 Super on Nebius Token Factory. Library docs from
          Tavily. API at <code>{API_BASE_URL}</code>.
        </p>
      </footer>
    </div>
  );
}

export default App;
