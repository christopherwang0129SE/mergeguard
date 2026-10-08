import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PairTable } from './PairTable';
import type { PairRisk } from '../types';

const pair = (a: string, b: string, score: number, level: PairRisk['level'], extra: Partial<PairRisk> = {}): PairRisk => ({
  branch_a: a,
  branch_b: b,
  score,
  level,
  shared_files: 1,
  overlapping_files: 1,
  collisions: [],
  rationale: 'rationale',
  score_source: 'git',
  git_score: score,
  model_score: null,
  fallback_reason: null,
  ...extra,
});

describe('PairTable', () => {
  it('sorts pairs by score descending', () => {
    const pairs = [
      pair('a', 'b', 10, 'low'),
      pair('c', 'd', 90, 'high'),
      pair('e', 'f', 50, 'medium'),
    ];
    const { container } = render(<PairTable pairs={pairs} risk_model={null} />);
    const rows = container.querySelectorAll('tbody tr');
    // First data row should be the highest score (90)
    expect(rows[0].textContent).toContain('90');
    expect(rows[1].textContent).toContain('50');
    expect(rows[2].textContent).toContain('10');
  });

  it('shows git_score and model_score side by side', () => {
    const pairs = [
      pair('a', 'b', 80, 'high', {
        score_source: 'model',
        git_score: 40,
        model_score: 80,
      }),
    ];
    const { container } = render(<PairTable pairs={pairs} risk_model="nemotron" />);
    // The row should contain both the git score (40) and the model score (80)
    // as separate cells.
    const row = container.querySelector('tbody tr');
    const cells = Array.from(row!.querySelectorAll('td')).map((c) => c.textContent);
    expect(cells).toContain('40');
    expect(cells).toContain('80');
  });

  it('shows a fallback badge when fallback_reason is set', () => {
    const pairs = [
      pair('a', 'b', 30, 'low', {
        score_source: 'git',
        git_score: 30,
        model_score: null,
        fallback_reason: 'no_api_key',
      }),
    ];
    render(<PairTable pairs={pairs} risk_model={null} />);
    expect(screen.getByText(/no_api_key/i)).toBeInTheDocument();
  });

  it('shows an empty state when there are no pairs', () => {
    const { container } = render(<PairTable pairs={[]} risk_model={null} />);
    expect(screen.getByText(/no branch pairs/i)).toBeInTheDocument();
    // No table rows should be rendered in the empty state.
    expect(container.querySelectorAll('tbody tr')).toHaveLength(0);
  });
});
