import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { VerificationDetails } from './VerificationDetails'
import { PairTable } from './PairTable'
import { usageLine } from '../utils/usage'
import type { PairRisk, Verification, UsageSummary } from '../types'

const CODE = 'import checkout\n\ndef test_branch_a_cap():\n    assert checkout.total(20.0, 10, 50.0, 10.0) == 198.0\n'

const verification = (extra: Partial<Verification> = {}): Verification => ({
  status: 'confirmed',
  reason: 'intent tests pass on A and on B; 1 that capture a branch\'s change fail on the merge of A+B',
  test_code: CODE,
  runs: [
    { target: 'a', ref: 'feat/discount-cap', selection: 'branch_a', exit_code: 0, passed: 1, failed: 0, errors: 0, timed_out: false, duration_s: 0.4, output: '' },
    { target: 'b', ref: 'feat/tax-before-discount', selection: 'branch_b', exit_code: 0, passed: 1, failed: 0, errors: 0, timed_out: false, duration_s: 0.4, output: '' },
    { target: 'base', ref: 'merge-base 6808b37', selection: 'branch', exit_code: 1, passed: 1, failed: 1, errors: 0, timed_out: false, duration_s: 0.4, output: '' },
    { target: 'merge', ref: 'feat/discount-cap+feat/tax-before-discount', selection: 'branch', exit_code: 1, passed: 1, failed: 1, errors: 0, timed_out: false, duration_s: 0.4, output: '' },
  ],
  attempts: 1,
  cached_test: false,
  sandbox: 'sandbox-exec(deny network, writes only in tmp) + socket guard',
  merge_conflict: false,
  confirming_tests: ['test_branch_a_cap'],
  duration_s: 21.3,
  prompt_tokens: 2900,
  completion_tokens: 3500,
  ...extra,
})

const pair = (extra: Partial<PairRisk> = {}): PairRisk => ({
  branch_a: 'feat/discount-cap',
  branch_b: 'feat/tax-before-discount',
  score: 47.5,
  level: 'medium',
  shared_files: 1,
  overlapping_files: 0,
  collisions: [],
  rationale: 'Cap assumes pre-tax subtotal.',
  score_source: 'model',
  git_score: 10,
  model_score: 85,
  model_level: 'high',
  fallback_reason: null,
  ...extra,
})

describe('VerificationDetails', () => {
  it('shows a Confirmed by test badge for a confirmed pair', () => {
    render(<VerificationDetails verification={verification()} />)
    expect(screen.getByText('Confirmed by test')).toHaveClass('badge-confirmed')
  })

  it('keeps the generated test collapsed until expanded', () => {
    const { container } = render(<VerificationDetails verification={verification()} />)
    const details = container.querySelector('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    fireEvent.click(screen.getByText(/Generated test \(1 attempt, 21.3s\)/))
    expect(details.open).toBe(true)
    expect(container.querySelector('pre code')?.textContent).toBe(CODE)
    expect(screen.getByText(/Merge A\+B/).parentElement).toHaveTextContent('1 passed, 1 failed')
    expect(screen.getByText('Fails on merge: test_branch_a_cap')).toBeInTheDocument()
  })

  it('labels suspected and skipped results without the confirmed badge', () => {
    const { rerender } = render(
      <VerificationDetails verification={verification({ status: 'suspected', reason: 'generated test also passes on the merge' })} />
    )
    expect(screen.queryByText('Confirmed by test')).toBeNull()
    expect(screen.getByText('Suspected: test inconclusive')).toBeInTheDocument()
    rerender(
      <VerificationDetails verification={verification({ status: 'skipped', test_code: null, runs: [] })} />
    )
    expect(screen.getByText('Test skipped')).toBeInTheDocument()
    expect(screen.queryByText(/Generated test/)).toBeNull()
  })

  it('renders inside the pair table row only for pairs that were verified', () => {
    render(
      <PairTable
        pairs={[pair({ verification: verification() }), pair({ branch_b: 'docs/readme', score: 0, level: 'none', verification: null })]}
        risk_model="nvidia/nemotron-3-super-120b-a12b"
      />
    )
    expect(screen.getAllByTestId('verification')).toHaveLength(1)
    expect(screen.getByText('Confirmed by test')).toBeInTheDocument()
  })

  it('shows the raised score note in the level cell of a confirmed pair', () => {
    render(
      <PairTable
        pairs={[
          pair({
            score: 90,
            level: 'high',
            blended_score: 47.5,
            score_note: 'raised: confirmed by test',
            verification: verification(),
          }),
        ]}
        risk_model={null}
      />
    )
    expect(screen.getByText('Risk 90 / 100')).toBeInTheDocument()
    expect(screen.getByText('High')).toBeInTheDocument()
    expect(screen.getByTestId('score-note')).toHaveTextContent('raised: confirmed by test (blend 48)')
    // Git and model columns stay as reported.
    expect(screen.getByText('10')).toBeInTheDocument()
    expect(screen.getByText('85')).toBeInTheDocument()
  })

  it('shows no note for a suspected pair', () => {
    render(
      <PairTable
        pairs={[pair({ verification: verification({ status: 'suspected' }) })]}
        risk_model={null}
      />
    )
    expect(screen.queryByTestId('score-note')).toBeNull()
    expect(screen.getByText('Medium')).toBeInTheDocument()
  })

  it('adds confirmed-by-test counts to the usage line', () => {
    const usage: UsageSummary = {
      model: 'm', llm_calls: 2, cache_hits: 0, prompt_tokens: 4097, completion_tokens: 4618,
      total_tokens: 8715, estimated_cost_usd: 0.0053853, input_usd_per_1m: 0.3, output_usd_per_1m: 0.9,
      pairs_total: 3, pairs_prefiltered: 2, pairs_sent_to_model: 1, pairs_capped: 0, top_k: 5,
      verify_pairs: 1, verify_confirmed: 1, elapsed_s: 38.8,
    }
    expect(usageLine(usage)).toBe(
      '2 LLM calls · 8,715 tokens · $0.0054 est. · 2 filtered before LLM · 1/1 confirmed by test'
    )
  })
})
