import { describe, it, expect } from 'vitest';
import { activeStage, finishRun, formatClock, formatDuration, initialRun, reduceRun } from './run';
import type { AnalyzeResponse, PairRisk, StreamEvent } from '../types';

const pair = (a: string, b: string, score: number): PairRisk => ({
  branch_a: a,
  branch_b: b,
  score,
  level: score >= 70 ? 'high' : 'none',
  shared_files: 0,
  overlapping_files: 0,
  collisions: [],
  rationale: '',
  score_source: 'git',
});

const fold = (events: StreamEvent[]) => events.reduce(reduceRun, initialRun());

describe('reduceRun', () => {
  it('tracks each stage from start to done with its timing', () => {
    const run = fold([
      { type: 'stage', stage: 'diff', status: 'start', label: 'Diffing', t: 1 },
      { type: 'stage', stage: 'diff', status: 'done', label: 'Diffed 3', detail: '3 branches', t: 2.5 },
      { type: 'stage', stage: 'model', status: 'start', label: 'Scoring', t: 3 },
      { type: 'stage', stage: 'verify', status: 'skipped', label: 'Skipped', t: 3 },
    ]);
    expect(run.stages.diff).toMatchObject({ state: 'done', startedAt: 1, endedAt: 2.5, detail: '3 branches' });
    expect(run.stages.model.state).toBe('running');
    expect(run.stages.verify.state).toBe('skipped');
    expect(activeStage(run)?.id).toBe('model');
    expect(run.lastT).toBe(3);
  });

  it('ignores stages it does not know', () => {
    const run = fold([{ type: 'stage', stage: 'warp' as never, status: 'start', label: '?', t: 1 }]);
    expect(Object.keys(run.stages)).not.toContain('warp');
  });

  it('adds pairs as they arrive and replaces a re-emitted pair in place', () => {
    const run = fold([
      { type: 'branches', base_branch: 'main', branches: ['a', 'b', 'c'], t: 0 },
      { type: 'pair', pair: pair('a', 'b', 40), t: 1 },
      { type: 'pair', pair: pair('a', 'c', 0), t: 2 },
      { type: 'pair', pair: pair('a', 'b', 90), t: 3 },
    ]);
    expect(run.branches).toEqual(['a', 'b', 'c']);
    expect(run.pairs.map((p) => [p.branch_b, p.score])).toEqual([
      ['b', 90],
      ['c', 0],
    ]);
  });

  it('lets the final result replace provisional pairs and close running stages', () => {
    const result: AnalyzeResponse = { base_branch: 'main', branches: ['a', 'b'], diffs: [], pairs: [pair('a', 'b', 90)] };
    const run = fold([
      { type: 'pair', pair: pair('a', 'b', 40), t: 1 },
      { type: 'stage', stage: 'branch_tests', status: 'start', label: 'Testing', t: 2 },
      { type: 'result', result, t: 9 },
    ]);
    expect(run.pairs[0].score).toBe(90);
    expect(run.stages.branch_tests).toMatchObject({ state: 'done', endedAt: 9 });
    expect(finishRun(initialRun(), result).baseBranch).toBe('main');
  });
});

describe('clock and durations', () => {
  it('formats the elapsed clock', () => {
    expect(formatClock(0)).toBe('0:00');
    expect(formatClock(7.9)).toBe('0:07');
    expect(formatClock(102)).toBe('1:42');
  });

  it('formats stage durations', () => {
    expect(formatDuration(0.42)).toBe('0.4s');
    expect(formatDuration(12.4)).toBe('12s');
    expect(formatDuration(125)).toBe('2m 05s');
  });
});
