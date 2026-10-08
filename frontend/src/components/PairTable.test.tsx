import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PairTable } from './PairTable'
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

describe('PairTable', () => {
  it('sorts pairs by score descending', () => {
    const pairs = [
      pair('a', 'b', 10, 'low'),
      pair('c', 'd', 90, 'high'),
      pair('e', 'f', 50, 'medium'),
    ]
    const { container } = render(<PairTable pairs={pairs} risk_model={null} />);
    const rows = container.querySelectorAll('tbody tr');
    // First data row should be the highest score (90)
    expect(rows[0].textContent).toContain('90');
    expect(rows[1].textContent).toContain('50');
    expect(rows[2].textContent).toContain('10');
  });

  it('shows the published risk as "Risk N / 100" plus the level word', () => {
    const pairs = [pair('a', 'b', 80, 'high')];
    render(<PairTable pairs={pairs} risk_model={null} />);
    expect(screen.getByText('Risk 80 / 100')).toBeInTheDocument();
    expect(screen.getByText('High')).toBeInTheDocument();
  });

  it('shows git_score and model_score side by side', () => {
    const pairs = [
      pair('a', 'b', 60, 'medium', {
        score_source: 'model',
        git_score: 40,
        model_score: 80,
      }),
    ];
    const { container } = render(<PairTable pairs={pairs} risk_model="nemotron" />);
    const row = container.querySelector('tbody tr');
    const cells = Array.from(row!.querySelectorAll('td')).map((c) => c.textContent);
    expect(cells).toContain('40');
    expect(cells).toContain('80');
  });

  it('shows a Nemotron source badge for model-sourced pairs', () => {
    const pairs = [
      pair('a', 'b', 60, 'medium', {
        score_source: 'model',
        git_score: 40,
        model_score: 80,
      }),
    ];
    render(<PairTable pairs={pairs} risk_model="nemotron" />);
    expect(screen.getByText('Nemotron')).toBeInTheDocument();
  });

  it('shows "Git fallback · <reason>" when the git score was kept', () => {
    const pairs = [
      pair('a', 'b', 30, 'low', {
        score_source: 'git',
        git_score: 30,
        model_score: null,
        fallback_reason: 'no_api_key',
      }),
    ];
    render(<PairTable pairs={pairs} risk_model={null} />);
    // The badge text is split across elements ("Git fallback" + " · no_api_key"),
    // so match the combined text of the source cell.
    const sourceCell = screen.getByText('Git fallback').closest('td')!;
    expect(sourceCell.textContent).toBe('Git fallback · no_api_key');
  });

  it('shows a plain "Git fallback" badge without a reason', () => {
    const pairs = [pair('a', 'b', 30, 'low')];
    render(<PairTable pairs={pairs} risk_model={null} />);
    expect(screen.getByText('Git fallback')).toBeInTheDocument();
  });

  it('shows the floor chip and two-line rationale when the floor rule applies', () => {
    const pairs = [
      pair('a', 'b', 80, 'high', {
        score_source: 'model',
        git_score: 80,
        model_score: 10,
        model_level: 'low',
        overlapping_files: 2,
        collisions: [
          {
            path: 'checkout.py',
            overlapping: true,
            branch_a_hunks: [[10, 12]],
            branch_b_hunks: [[40, 42]],
          },
          {
            path: 'pricing.py',
            overlapping: true,
            branch_a_hunks: [[5, 6]],
            branch_b_hunks: [[8, 9]],
          },
        ],
      }),
    ];
    render(<PairTable pairs={pairs} risk_model="nemotron" />);
    expect(
      screen.getByText('Floor rule: 2 shared files')
    ).toBeInTheDocument();
    expect(
      screen.getByText('Nemotron: no conflict expected (10/100).')
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Floor rule: both branches touch checkout.py, pricing.py, so kept High.'
      )
    ).toBeInTheDocument();
  });

  it('uses "N files" in the floor rationale when no overlapping paths are listed', () => {
    const pairs = [
      pair('a', 'b', 80, 'high', {
        score_source: 'model',
        git_score: 80,
        model_score: 60,
        model_level: 'medium',
        overlapping_files: 3,
        collisions: [],
      }),
    ];
    render(<PairTable pairs={pairs} risk_model="nemotron" />);
    expect(
      screen.getByText('Floor rule: 3 shared files')
    ).toBeInTheDocument();
    expect(
      screen.getByText('Nemotron: medium risk (60/100).')
    ).toBeInTheDocument();
    expect(
      screen.getByText('Floor rule: both branches touch 3 files, so kept High.')
    ).toBeInTheDocument();
  });

  it('shows the hidden conflict chip when the model level exceeds the git level', () => {
    const pairs = [
      pair('a', 'b', 45, 'medium', {
        score_source: 'model',
        git_score: 10,
        model_score: 80,
        model_level: 'high',
        model_rationale: 'Discount cap and tax order clash semantically.',
      }),
    ];
    const { container } = render(<PairTable pairs={pairs} risk_model="nemotron" />);
    expect(
      screen.getByText('Nemotron flags hidden conflict')
    ).toBeInTheDocument();
    // The full model rationale is shown.
    expect(
      screen.getByText('Discount cap and tax order clash semantically.')
    ).toBeInTheDocument();
    // The row is visually highlighted.
    const row = container.querySelector('tbody tr');
    expect(row!.classList.contains('row-hidden-conflict')).toBe(true);
  });

  it('does not show the hidden conflict chip when the model level does not exceed git', () => {
    const pairs = [
      pair('a', 'b', 50, 'medium', {
        score_source: 'model',
        git_score: 50,
        model_score: 50,
        model_level: 'medium',
      }),
    ];
    render(<PairTable pairs={pairs} risk_model="nemotron" />);
    expect(
      screen.queryByText('Nemotron flags hidden conflict')
    ).not.toBeInTheDocument();
  });

  it('does not flag a none -> low pair as a hidden conflict', () => {
    const pairs = [
      pair('docs/readme', 'feat/tax-before-discount', 5, 'low', {
        score_source: 'model',
        git_score: 0,
        model_score: 10,
        model_level: 'low',
        model_rationale: 'No conflicts expected.',
      }),
    ];
    const { container } = render(<PairTable pairs={pairs} risk_model="nemotron" />);
    expect(
      screen.queryByText('Nemotron flags hidden conflict')
    ).not.toBeInTheDocument();
    const row = container.querySelector('tbody tr');
    expect(row!.classList.contains('row-hidden-conflict')).toBe(false);
  });

  it('shows an empty state when there are no pairs', () => {
    const { container } = render(<PairTable pairs={[]} risk_model={null} />);
    expect(screen.getByText(/no branch pairs/i)).toBeInTheDocument();
    // No table rows should be rendered in the empty state.
    expect(container.querySelectorAll('tbody tr')).toHaveLength(0);
  });
});
