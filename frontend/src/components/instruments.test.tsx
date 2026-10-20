import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { TruthTable } from './TruthTable';
import { RiskGauge } from './RiskGauge';
import { Pipeline } from './Pipeline';
import { PairDiff } from './PairDiff';
import { clearPairDiffCache } from '../services/pairDiffCache';
import { initialRun, reduceRun } from '../utils/run';
import type { FileSide, PairDiffResponse, PairRisk } from '../types';

const run = (target: string, passed: number, failed: number) => ({
  target,
  passed,
  failed,
  errors: 0,
  timed_out: false,
});

describe('TruthTable', () => {
  it('places base, A, B and the merge in a 2x2 grid with outcomes', () => {
    render(
      <TruthTable
        label="Intent test runs"
        branchA="feat/a"
        branchB="feat/b"
        runs={[run('base', 1, 1), run('a', 1, 0), run('b', 1, 0), run('merge', 0, 2)]}
      />
    );
    const table = screen.getByRole('table', { name: 'Intent test runs' });
    const rows = within(table).getAllByRole('row');
    expect(within(rows[1]).getByTestId('tt-base')).toHaveTextContent('fail');
    expect(within(rows[1]).getByTestId('tt-b')).toHaveTextContent('feat/b');
    expect(within(rows[2]).getByTestId('tt-a')).toHaveTextContent('pass');
    expect(screen.getByTestId('tt-merge')).toHaveTextContent('2 failed');
    expect(screen.getByTestId('tt-merge')).toHaveAttribute('data-outcome', 'fail');
  });

  it('lets a merge rerun replace the first merge run and marks missing runs', () => {
    render(
      <TruthTable
        label="Branch tests"
        branchA="a"
        branchB="b"
        runs={[run('merge', 0, 1), run('merge-rerun', 3, 0)]}
      />
    );
    expect(screen.getByTestId('tt-merge')).toHaveAttribute('data-outcome', 'pass');
    expect(screen.getByTestId('tt-base')).toHaveTextContent('not run');
  });

  it('renders nothing without runs', () => {
    const { container } = render(<TruthTable label="x" branchA="a" branchB="b" runs={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

const PAIR: PairRisk = {
  branch_a: 'feat/a',
  branch_b: 'feat/b',
  score: 90,
  level: 'high',
  shared_files: 1,
  overlapping_files: 0,
  collisions: [],
  rationale: '',
  score_source: 'model',
  git_score: 10,
  model_score: 78,
};

describe('RiskGauge', () => {
  it('shows the score large, the level, and both sub-scores for screen readers', () => {
    render(<RiskGauge pair={PAIR} />);
    expect(screen.getByTestId('gauge-score')).toHaveTextContent('90');
    expect(screen.getByText('High risk')).toBeInTheDocument();
    expect(screen.getByText(/Risk score 90 out of 100/)).toHaveTextContent('git scored 10. Nemotron scored 78.');
  });

  it('omits the Nemotron mark when the model did not score the pair', () => {
    render(<RiskGauge pair={{ ...PAIR, score: 10, level: 'low', model_score: null }} />);
    expect(screen.queryByText(/Nemotron scored/)).not.toBeInTheDocument();
  });
});

describe('Pipeline', () => {
  it('explains each stage before a run', () => {
    render(<Pipeline run={initialRun()} mode="idle" />);
    expect(screen.getByRole('heading', { name: 'How a run works' })).toBeInTheDocument();
    expect(screen.getByTestId('stage-verify')).toHaveTextContent(/runs it on A, B, base and the merge/);
  });

  it('folds completed progress so the verdict leads to the branch pairs', () => {
    render(<Pipeline run={initialRun()} mode="finished" />);
    const disclosure = screen.getByText('Run details').closest('details');
    expect(disclosure).not.toHaveAttribute('open');
    expect(screen.getByTestId('pipeline')).not.toBeVisible();
    disclosure?.setAttribute('open', '');
    expect(screen.getByTestId('pipeline')).toBeVisible();
  });

  it('lights stages from stream events, ticks the clock and cancels', () => {
    let state = initialRun();
    state = reduceRun(state, { type: 'stage', stage: 'diff', status: 'start', label: 'Diffing', t: 0.1 });
    state = reduceRun(state, { type: 'stage', stage: 'diff', status: 'done', label: 'Diffed', detail: '3 branches', t: 0.6 });
    state = reduceRun(state, { type: 'stage', stage: 'model', status: 'start', label: 'Scoring', t: 0.7 });
    state = reduceRun(state, { type: 'stage', stage: 'verify', status: 'skipped', label: 'Skipped', t: 0.8 });
    const onCancel = vi.fn();
    render(<Pipeline run={state} mode="live" elapsedS={75} onCancel={onCancel} />);
    expect(screen.getByTestId('stage-diff')).toHaveAttribute('data-state', 'done');
    expect(screen.getByTestId('stage-diff')).toHaveTextContent('0.5s');
    expect(screen.getByTestId('stage-diff')).toHaveTextContent('3 branches');
    expect(screen.getByTestId('stage-model')).toHaveTextContent('running');
    expect(screen.getByTestId('stage-verify')).toHaveTextContent('skipped');
    expect(screen.getByTestId('stage-branch_tests')).toHaveTextContent('waiting');
    expect(screen.getByTestId('elapsed')).toHaveTextContent('1:15');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});

const side = (status: FileSide['status'], oldStart = 0, text = ''): FileSide => ({
  status,
  patch: '',
  truncated: false,
  hunks:
    status === 'unchanged'
      ? []
      : [
          {
            old_start: oldStart,
            old_lines: 1,
            new_start: oldStart,
            new_lines: 1,
            lines: [
              { kind: 'del', old_no: oldStart, new_no: null, text: 'old line' },
              { kind: 'add', old_no: null, new_no: oldStart, text },
            ],
          },
        ],
});

const DIFF: PairDiffResponse = {
  base_branch: 'main',
  branch_a: 'feat/a',
  branch_b: 'feat/b',
  merge_base_a: 'abc',
  merge_base_b: 'abc',
  files: [
    { path: 'only_b.py', shared: false, overlapping: false, a: side('unchanged'), b: side('added', 1, 'b only') },
    { path: 'checkout.py', shared: true, overlapping: true, a: side('modified', 5, 'a five'), b: side('modified', 5, 'b five') },
  ],
};

describe('PairDiff', () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    clearPairDiffCache();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const renderDiff = () =>
    render(<PairDiff source={{ repo_path: '/repo' }} baseBranch="main" branchA="feat/a" branchB="feat/b" />);

  it('requests both diffs and shows shared files first, overlaps marked, one-sided files folded', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(DIFF), { status: 200 }));
    renderDiff();
    expect(screen.getByTestId('diff-loading')).toBeInTheDocument();
    const diff = await screen.findByTestId('pair-diff');

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ repo_path: '/repo', base_branch: 'main', branch_a: 'feat/a', branch_b: 'feat/b', context: 3 });

    const files = diff.querySelectorAll('.file');
    expect(files[0]).toHaveAccessibleName('checkout.py');
    expect(within(files[0] as HTMLElement).getByText('Both branches change base lines 5')).toBeInTheDocument();
    expect(within(files[0] as HTMLElement).getByText('a five')).toBeInTheDocument();
    expect(within(files[0] as HTMLElement).getByText('b five')).toBeInTheDocument();

    expect(files[1].tagName).toBe('DETAILS');
    expect(files[1]).not.toHaveAttribute('open');
    expect(files[1]).toHaveTextContent('Only B changes this file (added)');
  });

  it('reports a failed load and retries', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Branch not found: feat/b' }), { status: 400 }));
    renderDiff();
    expect(await screen.findByTestId('diff-error')).toHaveTextContent('Branch not found: feat/b');
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(DIFF), { status: 200 }));
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('pair-diff')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
