import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BranchLights } from './BranchLights';
import type { PairRisk } from '../types';

const pair = (a: string, b: string, level: PairRisk['level'], score: number): PairRisk => ({
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
});

describe('BranchLights', () => {
  it('renders a light and text label for every branch', () => {
    const pairs = [pair('feature/a', 'feature/b', 'high', 90)];
    render(<BranchLights branches={['feature/a', 'feature/b', 'feature/c']} pairs={pairs} />);

    expect(screen.getByText(/feature\/a: High/i)).toBeInTheDocument();
    expect(screen.getByText(/feature\/b: High/i)).toBeInTheDocument();
    // feature/c has no pairs -> none
    expect(screen.getByText(/feature\/c: None/i)).toBeInTheDocument();
  });

  it('uses the highest level among a branch pairs', () => {
    const pairs = [
      pair('feature/a', 'feature/b', 'low', 10),
      pair('feature/a', 'feature/c', 'high', 95),
    ];
    render(<BranchLights branches={['feature/a']} pairs={pairs} />);
    expect(screen.getByText(/feature\/a: High/i)).toBeInTheDocument();
  });

  it('shows an empty state when there are no branches', () => {
    render(<BranchLights branches={[]} pairs={[]} />);
    expect(screen.getByText(/no branches/i)).toBeInTheDocument();
  });

  it('raises the light for a hidden conflict (model level above the published level)', () => {
    // Published level is low (blended score), but Nemotron flags a hidden
    // high conflict -> both branches must show High.
    const pairs: PairRisk[] = [
      {
        branch_a: 'feat/discount-cap',
        branch_b: 'feat/tax-before-discount',
        score: 40,
        level: 'low',
        shared_files: 1,
        overlapping_files: 0,
        collisions: [],
        rationale: 'model rationale',
        score_source: 'model',
        git_score: 10,
        model_score: 70,
        model_level: 'high',
        fallback_reason: null,
      },
    ];
    render(
      <BranchLights
        branches={['feat/discount-cap', 'feat/tax-before-discount']}
        pairs={pairs}
      />
    );
    expect(screen.getByText(/feat\/discount-cap: High/i)).toBeInTheDocument();
    expect(screen.getByText(/feat\/tax-before-discount: High/i)).toBeInTheDocument();
  });
});
