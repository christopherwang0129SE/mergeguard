import { describe, it, expect } from 'vitest';
import { verdictFor, pipelineFacts, needsAttention, failingTestCount, noTestsRan } from './verdict';
import type { AnalyzeResponse, BranchTests, PairRisk, Verification } from '../types';

const bt = (extra: Partial<BranchTests> = {}): BranchTests => ({
  status: 'confirmed',
  reason: 'r',
  files_a: [],
  files_b: [],
  unsupported_files: [],
  runs: [],
  confirming: [{ test_id: 't::x', branch: 'a', file: 't.py', excerpt: '' }],
  merge_conflict: false,
  duration_s: 1,
  ...extra,
});

const skippedVerification = (extra: Partial<Verification> = {}): Verification => ({
  status: 'skipped',
  reason: 'verify-by-test only runs on local repos under the allowed roots',
  runs: [],
  attempts: 0,
  cached_test: false,
  merge_conflict: false,
  duration_s: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
  ...extra,
});

const run = {
  target: 'merge' as const,
  ref: 'a+b',
  files: [],
  exit_code: 0,
  passed: 3,
  failed: 0,
  errors: 0,
  timed_out: false,
  duration_s: 1,
};

const pair = (extra: Partial<PairRisk> = {}): PairRisk => ({
  branch_a: 'a',
  branch_b: 'b',
  score: 0,
  level: 'none',
  shared_files: 0,
  overlapping_files: 0,
  collisions: [],
  rationale: 'r',
  score_source: 'git',
  git_score: 0,
  model_score: null,
  fallback_reason: null,
  ...extra,
});

describe('verdictFor', () => {
  it('leads with a hidden conflict proven by tests when git merges cleanly', () => {
    const v = verdictFor([pair({ score: 90, level: 'high', branch_tests: bt() }), pair({ branch_a: 'c' })]);
    expect(v.tone).toBe('proven');
    expect(v.headline).toBe('1 hidden conflict proven by tests');
    expect(v.detail).toBe('a and b merge without a git conflict, but 1 test fails on the merged code.');
  });

  it('drops "hidden" when the proven pair also overlaps in git', () => {
    const v = verdictFor([pair({ score: 90, level: 'high', overlapping_files: 1, branch_tests: bt() })]);
    expect(v.headline).toBe('1 conflict proven by tests');
    expect(v.detail).toBe('1 test fails when a and b are merged.');
  });

  it('counts risky pairs that no test proved', () => {
    const v = verdictFor([pair({ score: 55, level: 'medium' }), pair({ score: 75, level: 'high' }), pair()]);
    expect(v.tone).toBe('risky');
    expect(v.level).toBe('high');
    expect(v.headline).toBe('2 risky pairs to review before merging');
    expect(v.detail).toBe('Flagged by the diff or by Nemotron. No tests ran on the merged code. Checked 3 branch pairs.');
    const tested = verdictFor([
      pair({ score: 55, level: 'medium', branch_tests: bt({ status: 'passed', confirming: [], runs: [run] }) }),
    ]);
    expect(tested.detail).toBe('Flagged by the diff or by Nemotron, but no test failed on the merged code. Checked 1 branch pair.');
  });

  it('says no conflicts when every pair is none or low', () => {
    const v = verdictFor([pair(), pair({ score: 5, level: 'low' })]);
    expect(v.tone).toBe('clear');
    expect(v.headline).toBe('No conflicts found');
    expect(v.detail).toBe('All 2 branch pairs look safe to merge in any order.');
  });

  it('handles an analysis with no pairs', () => {
    expect(verdictFor([]).tone).toBe('empty');
  });
});

describe('helpers', () => {
  it('counts failing tests only from confirmed layers', () => {
    expect(failingTestCount(pair({ branch_tests: bt({ status: 'passed' }) }))).toBe(0);
    expect(failingTestCount(pair({ branch_tests: bt() }))).toBe(1);
  });

  it('needs attention for proven or medium+ pairs only', () => {
    expect(needsAttention(pair({ score: 5, level: 'low' }))).toBe(false);
    expect(needsAttention(pair({ score: 5, level: 'low', branch_tests: bt() }))).toBe(true);
    expect(needsAttention(pair({ score: 45, level: 'medium' }))).toBe(true);
  });

  it('summarises each pipeline step from the response', () => {
    const response: AnalyzeResponse = {
      base_branch: 'main',
      branches: ['a', 'b', 'c'],
      diffs: [],
      pairs: [
        pair({ score_source: 'model', branch_tests: bt() }),
        pair({ fallback_reason: 'prefiltered' }),
        pair({ fallback_reason: 'prefiltered' }),
      ],
    };
    expect(pipelineFacts(response)).toEqual([
      'Diffed 3 branches against main and scored 3 pairs.',
      'Reviewed 1 pair that share code; skipped 2 with nothing in common.',
      'Tested 1 pair on the merged code: 1 proven.',
    ]);
  });

  it('mentions Tavily grounding with the number of distinct http docs', () => {
    const docs = [
      { title: 'Requests', url: 'https://requests.readthedocs.io', query: 'requests' },
      { title: 'Real Python', url: 'https://realpython.com/python-requests', query: 'requests' },
      { title: 'Bad', url: 'javascript:alert(1)', query: 'x' },
    ];
    const response: AnalyzeResponse = {
      base_branch: 'main',
      branches: ['a', 'b', 'c'],
      diffs: [],
      pairs: [
        pair({ score_source: 'model', docs_sources: docs }),
        pair({ score_source: 'model', docs_sources: [docs[0]] }),
      ],
    };
    expect(pipelineFacts(response)[1]).toBe('Reviewed 2 pairs that share code, grounded with 2 library docs via Tavily.');
    expect(pipelineFacts({ ...response, pairs: [pair({ score_source: 'model' })] })[1]).toBe(
      'Reviewed 1 pair that share code.'
    );
  });

  it('does not claim tests ran when verification was skipped', () => {
    const pairs = [pair({ score: 60, level: 'medium', score_source: 'model', verification: skippedVerification() })];
    const response: AnalyzeResponse = { base_branch: 'main', branches: ['a', 'b'], diffs: [], pairs };
    expect(pipelineFacts(response)[2]).toBe(
      'No tests ran. Skipped: verify-by-test only runs on local repos under the allowed roots.'
    );
    expect(noTestsRan(pairs)).toBe(true);
  });

  it('reports a test error without calling it a pass', () => {
    const pairs = [pair({ verification: skippedVerification({ status: 'error', reason: 'Sandbox failed to start.' }) })];
    expect(pipelineFacts({ base_branch: 'main', branches: ['a', 'b'], diffs: [], pairs })[2]).toBe(
      'No tests ran: Sandbox failed to start.'
    );
  });

  it('counts only pairs whose tests ran, and notes skipped ones', () => {
    const pairs = [
      pair({ branch_tests: bt({ status: 'passed', confirming: [], runs: [run] }) }),
      pair({ branch_a: 'c', verification: skippedVerification() }),
    ];
    expect(pipelineFacts({ base_branch: 'main', branches: ['a', 'b', 'c'], diffs: [], pairs })[2]).toBe(
      'Tested 1 pair on the merged code: none failed; 1 skipped.'
    );
    expect(noTestsRan(pairs)).toBe(false);
    expect(noTestsRan([pair()])).toBe(false);
  });
});

import { pairVerdict, rankPairs, summaryLine } from './verdict';

describe('pairVerdict, rankPairs and summaryLine', () => {
  const base = {
    shared_files: 1,
    overlapping_files: 0,
    collisions: [],
    rationale: '',
    score_source: 'git' as const,
  };
  const confirmed = {
    ...base,
    branch_a: 'a',
    branch_b: 'b',
    score: 90,
    level: 'high' as const,
    branch_tests: { status: 'confirmed' } as never,
  };
  const suspected = { ...base, branch_a: 'a', branch_b: 'c', score: 55, level: 'medium' as const };
  const clean = { ...base, branch_a: 'b', branch_b: 'c', score: 5, level: 'low' as const };
  const capped = { ...clean, branch_a: 'c', branch_b: 'd', fallback_reason: 'pair_cap' as const };

  it('names each pair in one word', () => {
    expect([confirmed, suspected, clean, capped].map(pairVerdict)).toEqual(['confirmed', 'suspected', 'clean', 'skipped']);
  });

  it('ranks confirmed first, then by verdict and score', () => {
    expect(rankPairs([clean, capped, suspected, confirmed]).map(pairVerdict)).toEqual([
      'confirmed',
      'suspected',
      'skipped',
      'clean',
    ]);
  });

  it('writes the summary sentence', () => {
    expect(summaryLine([confirmed, clean, capped])).toBe('1 of 3 branch pairs breaks when merged, confirmed by test.');
    expect(summaryLine([confirmed, suspected])).toBe(
      '1 of 2 branch pairs breaks when merged, confirmed by test. 1 more looks risky.'
    );
    expect(summaryLine([suspected, clean])).toBe('1 of 2 branch pairs may break when merged. No test has confirmed it.');
    expect(summaryLine([clean])).toBe('The branch pair merges safely.');
    expect(summaryLine([clean, capped])).toBe('All 2 branch pairs merge safely.');
    expect(summaryLine([])).toBe('No branch pairs to compare.');
  });
});
