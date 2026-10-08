import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SummaryBar } from './SummaryBar'
import { usageLine, formatCost } from '../utils/usage'
import type { PairRisk, UsageSummary } from '../types'

const pair = (
  a: string,
  b: string,
  score: number,
  level: PairRisk['level'],
  extra: Partial<PairRisk> = {}
): PairRisk => ({
  branch_a: a,
  branch_b: b,
  score,
  level,
  shared_files: 1,
  overlapping_files: 0,
  collisions: [],
  rationale: 'rationale',
  score_source: 'git',
  git_score: score,
  model_score: null,
  fallback_reason: null,
  ...extra,
})

describe('SummaryBar', () => {
  it('shows the worst published level with a count of risky pairs', () => {
    const pairs = [
      pair('a', 'b', 80, 'high'),
      pair('c', 'd', 50, 'medium'),
      pair('e', 'f', 10, 'low'),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(screen.getByText('1 of 3 branch pairs at high risk')).toBeInTheDocument()
  })

  it('counts only medium/high pairs as risky', () => {
    const pairs = [
      pair('a', 'b', 50, 'medium'),
      pair('c', 'd', 10, 'low'),
      pair('e', 'f', 0, 'none'),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(screen.getByText('1 of 3 branch pairs at medium risk')).toBeInTheDocument()
  })

  it('says no risky pairs when everything is none or low', () => {
    const pairs = [
      pair('a', 'b', 10, 'low'),
      pair('c', 'd', 0, 'none'),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(screen.getByText('No risky pairs among 2')).toBeInTheDocument()
  })

  it('reports Nemotron disagreements on model-sourced pairs', () => {
    const pairs = [
      pair('a', 'b', 45, 'medium', {
        score_source: 'model',
        git_score: 10,
        model_score: 80,
        model_level: 'high',
      }),
      pair('c', 'd', 50, 'medium', {
        score_source: 'model',
        git_score: 50,
        model_score: 50,
        model_level: 'medium',
      }),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(
      screen.getByText('Nemotron disagrees on 1 pair(s)')
    ).toBeInTheDocument()
  })

  it('says Nemotron agrees when model levels match git levels', () => {
    const pairs = [
      pair('a', 'b', 50, 'medium', {
        score_source: 'model',
        git_score: 50,
        model_score: 50,
        model_level: 'medium',
      }),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(
      screen.getByText('Nemotron agrees with git on all pairs')
    ).toBeInTheDocument()
  })

  it('says git only when no pair used the model', () => {
    const pairs = [
      pair('a', 'b', 50, 'medium', { fallback_reason: 'no_api_key' }),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(screen.getByText('Git only: Nemotron unavailable')).toBeInTheDocument()
  })

  it('counts only medium/high disagreements (demo: 1 hidden conflict, 2 none->low pairs)', () => {
    const pairs = [
      // The real hidden conflict: git low (10) -> model high (70).
      pair('feat/discount-cap', 'feat/tax-before-discount', 40, 'medium', {
        score_source: 'model',
        git_score: 10,
        model_score: 70,
        model_level: 'high',
      }),
      // Harmless pairs: git none (0) -> model low (10). Not a disagreement.
      pair('docs/readme', 'feat/tax-before-discount', 5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 10,
        model_level: 'low',
      }),
      pair('docs/readme', 'feat/discount-cap', 2.5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 5,
        model_level: 'low',
      }),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(
      screen.getByText('Nemotron disagrees on 1 pair(s)')
    ).toBeInTheDocument()
  })

  it('reports hidden conflicts when no pair is published medium/high (amber)', () => {
    // git none (0) -> model medium (45): published 22.5 = low, but the model
    // level is medium and above the git level -> hidden conflict.
    const pairs = [
      pair('feat/discount-cap', 'feat/tax-before-discount', 22.5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 45,
        model_level: 'medium',
      }),
      pair('docs/readme', 'feat/tax-before-discount', 5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 10,
        model_level: 'low',
      }),
      pair('docs/readme', 'feat/discount-cap', 2.5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 5,
        model_level: 'low',
      }),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(screen.getByText('1 hidden conflict among 3 pairs')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveStyle({ borderLeftColor: '#f59e0b' })
  })

  it('pluralizes hidden conflicts', () => {
    const pairs = [
      pair('a', 'b', 22.5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 45,
        model_level: 'medium',
      }),
      pair('c', 'd', 35, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 70,
        model_level: 'high',
      }),
      pair('e', 'f', 5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 10,
        model_level: 'low',
      }),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(screen.getByText('2 hidden conflicts among 3 pairs')).toBeInTheDocument()
  })

  it('uses red for a hidden high conflict', () => {
    const pairs = [
      pair('a', 'b', 35, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 70,
        model_level: 'high',
      }),
      pair('c', 'd', 5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 10,
        model_level: 'low',
      }),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(screen.getByText('1 hidden conflict among 2 pairs')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveStyle({ borderLeftColor: '#ef4444' })
  })

  it('appends the hidden count when shown medium/high pairs exist', () => {
    const pairs = [
      // Shown medium, model agrees with git -> not hidden.
      pair('a', 'b', 50, 'medium', {
        score_source: 'model',
        git_score: 50,
        model_score: 50,
        model_level: 'medium',
      }),
      // Hidden: git none -> model medium, published low.
      pair('c', 'd', 22.5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 45,
        model_level: 'medium',
      }),
      pair('e', 'f', 5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 10,
        model_level: 'low',
      }),
    ]
    render(<SummaryBar pairs={pairs} />)
    expect(
      screen.getByText('2 of 3 branch pairs at medium risk · 1 hidden')
    ).toBeInTheDocument()
  })
})


const usage = (extra: Partial<UsageSummary> = {}): UsageSummary => ({
  model: 'nvidia/nemotron-3-super-120b-a12b',
  llm_calls: 1,
  cache_hits: 0,
  prompt_tokens: 1151,
  completion_tokens: 407,
  total_tokens: 1558,
  estimated_cost_usd: 0.0007116,
  input_usd_per_1m: 0.3,
  output_usd_per_1m: 0.9,
  pairs_total: 3,
  pairs_prefiltered: 2,
  pairs_sent_to_model: 1,
  pairs_capped: 0,
  top_k: 5,
  elapsed_s: 3.0,
  ...extra,
})

describe('SummaryBar usage', () => {
  it('shows tokens and estimated cost in the summary bar', () => {
    render(<SummaryBar pairs={[pair('a', 'b', 50, 'medium')]} usage={usage()} />)
    expect(screen.getByTestId('summary-usage')).toHaveTextContent(
      '1 LLM call · 1,558 tokens · $0.0007 est. · 2 filtered before LLM'
    )
  })

  it('shows cache hits and zero cost on a cached re-run', () => {
    const line = usageLine(
      usage({ llm_calls: 0, cache_hits: 1, total_tokens: 0, estimated_cost_usd: 0, pairs_prefiltered: 0 })
    )
    expect(line).toBe('0 LLM calls · 0 tokens · $0.0000 est. · 1 cached')
  })

  it('omits the usage line when the response has no usage', () => {
    render(<SummaryBar pairs={[pair('a', 'b', 50, 'medium')]} />)
    expect(screen.queryByTestId('summary-usage')).toBeNull()
  })

  it('formats cost with four decimals', () => {
    expect(formatCost(0.00123456)).toBe('$0.0012')
  })

  it('says the prefilter skipped the model when every pair was filtered', () => {
    render(
      <SummaryBar
        pairs={[pair('a', 'b', 0, 'none', { fallback_reason: 'prefiltered' })]}
        usage={usage({ llm_calls: 0, total_tokens: 0, estimated_cost_usd: 0 })}
      />
    )
    expect(
      screen.getByText('No pair needed Nemotron: static prefilter found no overlap')
    ).toBeInTheDocument()
  })
})

describe('SummaryBar confirmed by test', () => {
  const confirmedPair = pair('feat/discount-cap', 'feat/tax-before-discount', 90, 'high', {
    score_source: 'model',
    git_score: 10,
    model_score: 85,
    model_level: 'high',
    blended_score: 47.5,
    score_note: 'raised: confirmed by test',
    verification: {
      status: 'confirmed',
      reason: 'r',
      runs: [],
      attempts: 1,
      cached_test: false,
      merge_conflict: false,
      duration_s: 1,
      prompt_tokens: 0,
      completion_tokens: 0,
    },
  })
  const docs = (b: string) => pair('docs/readme', b, 0, 'none', { fallback_reason: 'prefiltered', shared_files: 0 })

  it('headlines high risk with the confirmed count and keeps the hidden-conflict info', () => {
    render(
      <SummaryBar pairs={[confirmedPair, docs('feat/discount-cap'), docs('feat/tax-before-discount')]} />
    )
    expect(
      screen.getByText('1 of 3 branch pairs at high risk · 1 confirmed by test · 1 hidden')
    ).toBeInTheDocument()
  })

  it('does not count suspected verdicts as confirmed', () => {
    const suspected = pair('a', 'b', 80, 'high', {
      verification: { ...confirmedPair.verification!, status: 'suspected' },
    })
    render(<SummaryBar pairs={[suspected]} />)
    expect(screen.getByText('1 of 1 branch pairs at high risk')).toBeInTheDocument()
  })
})
