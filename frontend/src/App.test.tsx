import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import path from 'node:path';
import { render, screen, fireEvent, within, act } from '@testing-library/react';
import App from './App';
import { clearPairDiffCache } from './services/pairDiffCache';
import type { AnalyzeResponse, PairDiffResponse, PairRisk, StreamEvent } from './types';

const jsonResponse = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

const HEALTH = {
  status: 'ok',
  nebius_ready: true,
  nebius_enabled: true,
  nebius_model: 'nvidia/nemotron',
  tavily_ready: true,
};

const CONFIRMED: PairRisk = {
  branch_a: 'feat/discount-cap',
  branch_b: 'feat/tax-before-discount',
  score: 90,
  level: 'high',
  shared_files: 1,
  overlapping_files: 0,
  collisions: [],
  rationale: 'Cap assumes a pre-tax subtotal.',
  score_source: 'model',
  git_score: 10,
  model_score: 85,
  model_level: 'high',
  model_rationale: 'Cap assumes a pre-tax subtotal.',
  fallback_reason: null,
  branch_tests: {
    status: 'confirmed',
    reason: 'fail on merge',
    files_a: [],
    files_b: [],
    unsupported_files: [],
    runs: [
      { target: 'a', ref: 'feat/discount-cap', files: [], exit_code: 0, passed: 3, failed: 0, errors: 0, timed_out: false, duration_s: 0.2 },
      { target: 'b', ref: 'feat/tax-before-discount', files: [], exit_code: 0, passed: 2, failed: 0, errors: 0, timed_out: false, duration_s: 0.2 },
      { target: 'merge', ref: 'a+b', files: [], exit_code: 1, passed: 3, failed: 2, errors: 0, timed_out: false, duration_s: 0.2 },
    ],
    confirming: [
      { test_id: 't::a', branch: 'feat/discount-cap', file: 't.py', excerpt: '200 != 198' },
      { test_id: 't::b', branch: 'feat/tax-before-discount', file: 't.py', excerpt: '90 != 88' },
    ],
    merge_conflict: false,
    duration_s: 0.7,
  },
};

const CLEAN: PairRisk = {
  branch_a: 'docs/readme',
  branch_b: 'feat/discount-cap',
  score: 0,
  level: 'none',
  shared_files: 0,
  overlapping_files: 0,
  collisions: [],
  rationale: 'No shared files.',
  score_source: 'git',
  git_score: 0,
  fallback_reason: 'prefiltered',
};

const RESULT: AnalyzeResponse = {
  base_branch: 'main',
  branches: ['docs/readme', 'feat/discount-cap', 'feat/tax-before-discount'],
  diffs: [],
  risk_model: 'nvidia/nemotron',
  pairs: [CLEAN, CONFIRMED],
  usage: {
    model: 'nvidia/nemotron', llm_calls: 1, cache_hits: 0, prompt_tokens: 1976, completion_tokens: 680,
    total_tokens: 2656, estimated_cost_usd: 0.0012, input_usd_per_1m: 0.3, output_usd_per_1m: 0.9,
    pairs_total: 2, pairs_prefiltered: 1, pairs_sent_to_model: 1, pairs_capped: 0, top_k: 5, elapsed_s: 16.7,
  },
};

const DIFF: PairDiffResponse = {
  base_branch: 'main',
  branch_a: 'feat/discount-cap',
  branch_b: 'feat/tax-before-discount',
  merge_base_a: 'abc',
  merge_base_b: 'abc',
  files: [
    {
      path: 'checkout.py',
      shared: true,
      overlapping: false,
      a: {
        status: 'modified',
        patch: '',
        truncated: false,
        hunks: [
          {
            old_start: 3, old_lines: 1, new_start: 3, new_lines: 2,
            lines: [
              { kind: 'del', old_no: 3, new_no: null, text: 'def apply_discount(x):' },
              { kind: 'add', old_no: null, new_no: 3, text: 'def apply_discount(x, cap):' },
            ],
          },
        ],
      },
      b: {
        status: 'modified',
        patch: '',
        truncated: false,
        hunks: [
          {
            old_start: 12, old_lines: 1, new_start: 12, new_lines: 1,
            lines: [{ kind: 'add', old_no: null, new_no: 12, text: '    taxed = subtotal * 1.1' }],
          },
        ],
      },
    },
  ],
};

const expectedDemoPath = path.resolve(process.cwd(), '../demo/checkout-demo');
const expectedApiDemoPath = path.resolve(process.cwd(), '../demo/api-demo');

/** A stream response the test feeds line by line; aborting the request errors it. */
const controllableStream = (signal?: AbortSignal | null) => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  signal?.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')));
  const encoder = new TextEncoder();
  return {
    response: new Response(body, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } }),
    push: (event: StreamEvent) => controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`)),
    close: () => controller.close(),
  };
};

type Route = (init: RequestInit | undefined) => Promise<Response>;

describe('App', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let routes: Record<string, Route>;

  const route = (suffix: string, handler: Route) => {
    routes[suffix] = handler;
  };
  const calls = (suffix: string) => fetchMock.mock.calls.filter(([url]) => String(url).endsWith(suffix));

  beforeEach(() => {
    clearPairDiffCache();
    window.history.replaceState(null, '', '/');
    routes = {
      '/api/health': () => Promise.resolve(jsonResponse(HEALTH)),
      '/api/pair-diff': () => Promise.resolve(jsonResponse(DIFF)),
    };
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const key = Object.keys(routes).find((suffix) => String(url).endsWith(suffix));
      return key ? routes[key](init) : Promise.resolve(jsonResponse({ detail: 'Not Found' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, '', '/');
  });

  it('opens on the form, both demos and how a run works', async () => {
    render(<App />);
    expect(screen.getByRole('banner')).toHaveTextContent('MergeGuard');
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      'Find branch pairs that merge cleanly and still break'
    );
    expect(screen.getByRole('form', { name: 'Analyze a repository' })).toBeInTheDocument();
    expect(screen.getByLabelText('Repository')).toHaveAccessibleDescription(/pull request URL/);
    expect(screen.getByLabelText('Base branch')).toHaveValue('main');
    expect(screen.getByRole('button', { name: 'Analyze' })).toHaveClass('btn-primary');
    expect(screen.getByRole('button', { name: 'Try demo repo' })).toHaveAccessibleDescription(/discount and tax/);
    expect(screen.getByRole('button', { name: 'Try API demo (Tavily)' })).toHaveAccessibleDescription(/Tavily/);
    const pipeline = screen.getByTestId('pipeline');
    expect(within(pipeline).getByRole('heading', { name: 'How a run works' })).toBeInTheDocument();
    expect(within(pipeline).getAllByRole('listitem')).toHaveLength(6);
    expect(await screen.findByText('Sentinel online')).toBeInTheDocument();
  });

  it('streams the run: stages light up, pairs appear, then the summary leads', async () => {
    let stream!: ReturnType<typeof controllableStream>;
    route('/api/analyze/stream', (init) => {
      stream = controllableStream(init?.signal);
      return Promise.resolve(stream.response);
    });
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Try demo repo' }));
    expect((screen.getByLabelText('Repository') as HTMLInputElement).value).toBe(expectedDemoPath);
    expect(await screen.findByRole('heading', { name: 'Analyzing' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Analyzing…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByTestId('elapsed')).toHaveTextContent('0:00');
    expect(JSON.parse(String(calls('/api/analyze/stream')[0][1].body))).toEqual({
      repo_path: expectedDemoPath,
      base_branch: 'main',
    });

    await act(async () => {
      stream.push({ type: 'stage', stage: 'resolve', status: 'start', label: 'Opening repo', t: 0 });
      stream.push({ type: 'stage', stage: 'resolve', status: 'done', label: 'Opened', t: 0.2 });
      stream.push({ type: 'branches', base_branch: 'main', branches: RESULT.branches, t: 0.3 });
      stream.push({ type: 'stage', stage: 'model', status: 'start', label: 'Scoring', detail: '1 pair', t: 1 });
      stream.push({ type: 'pair', pair: { ...CONFIRMED, branch_tests: null, score: 47 }, t: 4 });
    });
    expect(await screen.findByTestId('pair-row')).toHaveTextContent('47');
    expect(screen.getByTestId('stage-resolve')).toHaveAttribute('data-state', 'done');
    expect(screen.getByTestId('stage-resolve')).toHaveTextContent('0.2s');
    expect(screen.getByTestId('stage-model')).toHaveAttribute('data-state', 'running');
    expect(screen.getByTestId('stage-model')).toHaveTextContent('1 pair');
    expect(screen.getByRole('status')).toHaveTextContent('Nemotron running.');
    // The first scored pair opens while the run continues.
    expect(screen.getByTestId('pair-detail')).toHaveTextContent('Still running');

    await act(async () => {
      stream.push({ type: 'pair', pair: { ...CONFIRMED, branch_tests: null, score: 10, level: 'low', score_source: 'git', model_score: null }, t: 4.5 });
    });
    expect(screen.getByTestId('pair-row')).toHaveTextContent('Awaiting verdict');
    expect(screen.getByTestId('pair-detail')).toHaveTextContent('Risk scoring and test checks are still running.');
    expect(screen.queryByText('Nothing here needs attention.')).not.toBeInTheDocument();

    await act(async () => {
      stream.push({ type: 'stage', stage: 'model', status: 'done', label: 'Scored', t: 5 });
      stream.push({ type: 'result', result: RESULT, t: 16.7 });
      stream.close();
    });
    const summary = await screen.findByRole('heading', {
      name: '1 of 2 branch pairs breaks when merged, confirmed by test.',
    });
    expect(summary).toHaveFocus();
    expect(screen.getByTestId('run-figures')).toHaveTextContent('Est. cost$0.0012');
    expect(screen.getByRole('status')).toHaveTextContent('Analysis finished.');
    // Worst pair first, and it is the one open.
    const rows = screen.getAllByTestId('pair-row');
    expect(rows[0]).toHaveTextContent('90');
    expect(rows[0]).toHaveAttribute('aria-current', 'true');
    expect(rows[1]).toHaveTextContent('Clean');
    const detail = screen.getByTestId('pair-detail');
    expect(within(detail).getByTestId('gauge-score')).toHaveTextContent('90');
    expect(within(detail).getByTestId('tt-merge')).toHaveAttribute('data-outcome', 'fail');
    expect(await within(detail).findByTestId('pair-diff')).toHaveTextContent('def apply_discount(x, cap):');
    expect(window.location.hash).toBe(
      `#repo=${expectedDemoPath}&base=main&pair=feat/discount-cap..feat/tax-before-discount`
    );
    expect(JSON.parse(String(calls('/api/pair-diff')[0][1].body))).toEqual({
      repo_path: expectedDemoPath,
      base_branch: 'main',
      branch_a: 'feat/discount-cap',
      branch_b: 'feat/tax-before-discount',
      context: 3,
    });
  });

  it('switches pairs and keeps the choice in the URL', async () => {
    route('/api/analyze/stream', () => {
      const s = controllableStream();
      s.push({ type: 'result', result: RESULT, t: 1 });
      s.close();
      return Promise.resolve(s.response);
    });
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Try demo repo' }));
    await screen.findByTestId('run-summary');
    fireEvent.click(screen.getAllByTestId('pair-row')[1]);
    expect(screen.getAllByTestId('pair-row')[1]).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('heading', { level: 2, name: /docs\/readme/ })).toBeInTheDocument();
    expect(window.location.hash).toMatch(/pair=docs\/readme\.\.feat\/discount-cap$/);
  });

  it('runs the analysis named in a shared link and opens its pair', async () => {
    window.history.replaceState(null, '', '/#repo=/tmp/shop&base=develop&pair=docs/readme..feat/discount-cap');
    route('/api/analyze/stream', () => {
      const s = controllableStream();
      s.push({ type: 'result', result: { ...RESULT, base_branch: 'develop' }, t: 1 });
      s.close();
      return Promise.resolve(s.response);
    });
    render(<App />);
    expect(screen.getByLabelText('Repository')).toHaveValue('/tmp/shop');
    expect(screen.getByLabelText('Base branch')).toHaveValue('develop');
    await screen.findByTestId('run-summary');
    expect(JSON.parse(String(calls('/api/analyze/stream')[0][1].body))).toEqual({
      repo_path: '/tmp/shop',
      base_branch: 'develop',
    });
    expect(screen.getByTestId('pair-detail')).toHaveTextContent('docs/readme');
    expect(screen.getAllByTestId('pair-row')[1]).toHaveAttribute('aria-current', 'true');
  });

  it('falls back to POST /api/analyze when the stream endpoint is missing', async () => {
    route('/api/analyze', () => Promise.resolve(jsonResponse(RESULT)));
    route('/api/analyze/stream', () => Promise.resolve(jsonResponse({ detail: 'Not Found' }, 404)));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Try API demo (Tavily)' }));
    expect(await screen.findByTestId('run-summary')).toHaveTextContent('breaks when merged');
    expect(JSON.parse(String(calls('/api/analyze')[0][1].body))).toEqual({
      repo_path: expectedApiDemoPath,
      base_branch: 'main',
    });
  });

  it('cancels a running analysis', async () => {
    route('/api/analyze/stream', (init) => Promise.resolve(controllableStream(init?.signal).response));
    render(<App />);
    fireEvent.change(screen.getByLabelText('Repository'), { target: { value: '/tmp/repo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(await screen.findByRole('heading', { name: /Analysis cancelled after/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeEnabled();
    expect(screen.queryByRole('heading', { name: 'Analysis failed' })).toBeNull();
  });

  it('shows a mid-stream error from the backend', async () => {
    route('/api/analyze/stream', () => {
      const s = controllableStream();
      s.push({ type: 'stage', stage: 'diff', status: 'start', label: 'Diffing', t: 0 });
      s.push({ type: 'error', detail: 'git diff failed: bad object', t: 1 });
      s.close();
      return Promise.resolve(s.response);
    });
    render(<App />);
    fireEvent.change(screen.getByLabelText('Repository'), { target: { value: '/tmp/repo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    const heading = await screen.findByRole('heading', { name: 'Analysis failed' });
    expect(heading).toHaveFocus();
    expect(screen.getByText('git diff failed: bad object')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('flags an empty repository field without calling the API', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    const input = screen.getByLabelText('Repository');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription(/enter a repository path or url/i);
    expect(calls('/api/analyze/stream')).toHaveLength(0);
  });

  it('explains a path that is not a git repository and how to fix it', async () => {
    route('/api/analyze/stream', () =>
      Promise.resolve(jsonResponse({ detail: '/tmp/nope is not a git repository' }, 400))
    );
    render(<App />);
    fireEvent.change(screen.getByLabelText('Repository'), { target: { value: '/tmp/nope' } });
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    expect(await screen.findByRole('heading', { name: "That path isn't a git repository" })).toHaveFocus();
    expect(screen.getByText('/tmp/nope is not a git repository')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Command to run' })).toHaveTextContent('make_demo_repo.py');
  });

  it('explains the demo-mode lock', async () => {
    route('/api/analyze/stream', () =>
      Promise.resolve(jsonResponse({ detail: 'Demo mode only analyzes the bundled demo repositories (/app/demo).' }, 403))
    );
    render(<App />);
    fireEvent.change(screen.getByLabelText('Repository'), { target: { value: 'https://github.com/o/r' } });
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    expect(await screen.findByRole('heading', { name: 'This deployment only runs the bundled demos' })).toBeInTheDocument();
    expect(JSON.parse(String(calls('/api/analyze/stream')[0][1].body))).toEqual({
      repo_url: 'https://github.com/o/r',
      base_branch: 'main',
    });
  });

  it('tells the user to start the backend when it cannot be reached', async () => {
    routes['/api/health'] = () => Promise.reject(new TypeError('Failed to fetch'));
    route('/api/analyze/stream', () => Promise.reject(new TypeError('Failed to fetch')));
    render(<App />);
    expect(await screen.findByText(/backend isn.t answering/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Repository'), { target: { value: '/tmp/repo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    expect(await screen.findByRole('heading', { name: "Can't reach the MergeGuard backend" })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Command to run' })).toHaveTextContent('uvicorn app.main:app');
  });
});
