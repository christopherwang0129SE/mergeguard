import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SummaryBar } from './SummaryBar'
import type { PairRisk } from '../types'

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
})
