import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { BranchTestDetails } from './BranchTestDetails'
import { pairVerdict } from '../utils/verdict'
import { usageLine } from '../utils/usage'
import { isConfirmedByBranchTest, isConfirmedByIntentTest, isConfirmedByTest } from '../utils/risk'
import type { BranchTests, PairRisk, UsageSummary } from '../types'

const branchTests = (extra: Partial<BranchTests> = {}): BranchTests => ({
  status: 'confirmed',
  reason: "1 of the branches' own tests pass on their branch and fail on the merge of A+B",
  files_a: ['tests/test_store.py'],
  files_b: ['tests/test_report.py'],
  unsupported_files: [],
  runs: [
    { target: 'a', ref: 'feat/tuple', files: ['tests/test_store.py'], exit_code: 0, passed: 2, failed: 0, errors: 0, timed_out: false, duration_s: 0.5 },
    { target: 'b', ref: 'feat/report', files: ['tests/test_report.py'], exit_code: 0, passed: 1, failed: 0, errors: 0, timed_out: false, duration_s: 0.5 },
    { target: 'merge', ref: 'feat/tuple+feat/report', files: ['tests/test_store.py', 'tests/test_report.py'], exit_code: 1, passed: 2, failed: 1, errors: 0, timed_out: false, duration_s: 0.6 },
  ],
  confirming: [
    {
      test_id: 'tests.test_report::test_report_uses_floor',
      branch: 'feat/report',
      file: 'tests/test_report.py',
      excerpt: "E AttributeError: 'tuple' object has no attribute 'get'",
    },
  ],
  merge_conflict: false,
  sandbox: 'sandbox-exec(deny network, writes only in tmp) + socket guard',
  duration_s: 3.2,
  ...extra,
})

const pair = (extra: Partial<PairRisk> = {}): PairRisk => ({
  branch_a: 'feat/tuple',
  branch_b: 'feat/report',
  score: 90,
  level: 'high',
  shared_files: 0,
  overlapping_files: 0,
  collisions: [],
  rationale: 'r',
  score_source: 'git',
  fallback_reason: 'no_api_key',
  ...extra,
})

describe('BranchTestDetails', () => {
  it('labels a branch-test confirmation separately from the intent test', () => {
    render(<BranchTestDetails branchTests={branchTests()} branchA="feat/tuple" branchB="feat/report" />)
    const badge = screen.getByText('Confirmed by branch test')
    expect(badge).toHaveAttribute('data-tone', 'fail')
    expect(screen.queryByText(/intent test/)).toBeNull()
  })

  it('shows the failing test id and the error excerpt as evidence', () => {
    render(<BranchTestDetails branchTests={branchTests()} branchA="feat/tuple" branchB="feat/report" />)
    expect(screen.getByText('tests.test_report::test_report_uses_floor')).toBeInTheDocument()
    const failures = screen.getByLabelText('Branch tests failing on merge')
    expect(failures).toHaveTextContent("feat/report's test")
    const excerpt = screen.getByRole('region', { name: 'Failure in tests.test_report::test_report_uses_floor' })
    expect(excerpt).toHaveTextContent("E AttributeError: 'tuple' object has no attribute 'get'")
    expect(excerpt.querySelector('[data-marked]')).not.toBeNull()
  })

  it('keeps per-checkout runs collapsed until expanded', () => {
    const { container } = render(<BranchTestDetails branchTests={branchTests()} branchA="feat/tuple" branchB="feat/report" />)
    const details = container.querySelector('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    expect(screen.getByText(/All runs/, { selector: 'summary' })).toHaveTextContent('All runs (3.2s)')
    fireEvent.click(screen.getByText(/All runs/, { selector: 'summary' }))
    expect(details.open).toBe(true)
    expect(screen.getByText('Merge A+B').parentElement).toHaveTextContent('2 files): 2 passed, 1 failed')
  })

  it('shows the JS runner, package and install/build time', () => {
    const js = branchTests({
      files_a: ['packages/bridge/src/bridge.test.ts'],
      files_b: ['packages/bridge/src/bridge.test.ts'],
      runs: [
        { target: 'merge', ref: 'a+b', files: ['packages/bridge/src/bridge.test.ts'], exit_code: 1, passed: 565, failed: 1, errors: 0, timed_out: false, duration_s: 18.4, runner: 'vitest', package: 'packages/bridge', problem: '' },
        { target: 'merge', ref: 'a+b', files: ['packages/web/src/x.test.tsx'], exit_code: null, passed: 0, failed: 0, errors: 0, timed_out: false, duration_s: 0, runner: 'vitest', package: 'packages/web', problem: 'JS setup failed: no lockfile' },
      ],
      setup: ['npm install (npm-0abef5c7, scripts disabled): 29.6s', 'packages/core: built in 41.7s'],
      install_s: 29.6,
      build_s: 41.7,
    })
    render(<BranchTestDetails branchTests={js} branchA="feat/tuple" branchB="feat/report" />)
    fireEvent.click(screen.getByText(/All runs/, { selector: 'summary' }))
    const items = screen.getAllByText('Merge A+B').map((el) => el.parentElement)
    expect(items[0]).toHaveTextContent('vitest in packages/bridge, 1 file): 565 passed, 1 failed')
    expect(items[1]).toHaveTextContent('no results: JS setup failed: no lockfile')
    expect(screen.getByTestId('branch-test-setup')).toHaveTextContent(
      'JS setup (install 29.6s, builds 41.7s): npm install (npm-0abef5c7, scripts disabled): 29.6s; packages/core: built in 41.7s'
    )
  })

  it('shows a pass status when the branch tests pass on the merge', () => {
    render(<BranchTestDetails branchTests={branchTests({ status: 'passed', confirming: [] })} branchA="feat/tuple" branchB="feat/report" />)
    expect(screen.getByText('Branch tests pass on merge')).toHaveAttribute('data-tone', 'pass')
    expect(screen.queryByLabelText('Branch tests failing on merge')).toBeNull()
  })

  it('makes a branch-test-only pair a confirmed pair', () => {
    expect(pairVerdict(pair({ branch_tests: branchTests() }))).toBe('confirmed')
  })

  it('does not confirm a pair whose branch tests pass on the merge', () => {
    expect(pairVerdict(pair({ score: 20, level: 'low', branch_tests: branchTests({ status: 'passed', confirming: [] }) }))).toBe('clean')
  })

  it('shows the four checkouts as a truth table with the merge failing', () => {
    render(<BranchTestDetails branchTests={branchTests()} branchA="feat/tuple" branchB="feat/report" />)
    expect(screen.getByTestId('tt-merge')).toHaveAttribute('data-outcome', 'fail')
    expect(screen.getByTestId('tt-a')).toHaveTextContent('feat/tuple')
    expect(screen.getByTestId('tt-base')).toHaveTextContent('not run')
  })

  it('tells the two confirmation layers apart', () => {
    const both = pair({ branch_tests: branchTests(), verification: { status: 'confirmed' } as PairRisk['verification'] })
    expect(isConfirmedByBranchTest(both)).toBe(true)
    expect(isConfirmedByIntentTest(both)).toBe(true)
    const branchOnly = pair({ branch_tests: branchTests() })
    expect(isConfirmedByIntentTest(branchOnly)).toBe(false)
    expect(isConfirmedByTest(branchOnly)).toBe(true)
    expect(isConfirmedByTest(pair({ branch_tests: branchTests({ status: 'passed' }) }))).toBe(false)
  })

  it('adds the branch-test count to the usage line', () => {
    const usage: UsageSummary = {
      model: 'm', llm_calls: 0, cache_hits: 0, prompt_tokens: 0, completion_tokens: 0,
      total_tokens: 0, estimated_cost_usd: 0, input_usd_per_1m: 0.3, output_usd_per_1m: 0.9,
      pairs_total: 3, pairs_prefiltered: 2, pairs_sent_to_model: 0, pairs_capped: 0, top_k: 5,
      branch_test_pairs: 2, branch_test_confirmed: 1, elapsed_s: 4.1,
    }
    expect(usageLine(usage)).toBe(
      '0 LLM calls · 0 tokens · $0.0000 est. · 2 filtered before LLM · 1/2 confirmed by branch test'
    )
  })
})
