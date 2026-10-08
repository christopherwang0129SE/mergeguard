import { describe, it, expect } from 'vitest'
import { levelToColor, levelToText, getBranchRiskLevel, sortPairsByScoreDesc } from './risk'
import type { PairRisk, RiskLevel } from '../types'

describe('risk utils', () => {
  const mockPair: PairRisk = {
    branch_a: 'feature/a',
    branch_b: 'feature/b',
    score: 75,
    level: 'medium',
    shared_files: 2,
    overlapping_files: 1,
    collisions: [],
    rationale: 'test',
    score_source: 'model',
    git_score: 50,
    git_rationale: 'git',
    model_score: 75,
    model_level: 'medium',
    model_rationale: 'model',
    fallback_reason: null,
  }

  describe('levelToColor', () => {
    it('returns green for none and low', () => {
      expect(levelToColor('none')).toBe('#10b981')
      expect(levelToColor('low')).toBe('#10b981')
    })
    it('returns amber for medium', () => {
      expect(levelToColor('medium')).toBe('#f59e0b')
    })
    it('returns red for high', () => {
      expect(levelToColor('high')).toBe('#ef4444')
    })
    it('returns gray for unknown', () => {
      // @ts-ignore
      expect(levelToColor('unknown' as RiskLevel)).toBe('#6b7280')
    })
  })

  describe('levelToText', () => {
    it('maps levels to text', () => {
      expect(levelToText('none')).toBe('None')
      expect(levelToText('low')).toBe('Low')
      expect(levelToText('medium')).toBe('Medium')
      expect(levelToText('high')).toBe('High')
      // @ts-ignore
      expect(levelToText('unknown' as RiskLevel)).toBe('Unknown')
    })
  })

  describe('getBranchRiskLevel', () => {
    it('returns none for branch with no pairs', () => {
      expect(getBranchRiskLevel('main', [])).toBe('none')
    })
    it('returns the highest level among pairs', () => {
      const pairs: PairRisk[] = [
        { ...mockPair, level: 'low' },
        { ...mockPair, branch_a: 'feature/a', branch_b: 'feature/c', level: 'high' },
        { ...mockPair, branch_a: 'feature/b', branch_b: 'feature/d', level: 'medium' },
      ]
      expect(getBranchRiskLevel('feature/a', pairs)).toBe('high')
      expect(getBranchRiskLevel('feature/b', pairs)).toBe('medium')
      expect(getBranchRiskLevel('feature/c', pairs)).toBe('high')
      expect(getBranchRiskLevel('feature/d', pairs)).toBe('medium')
      expect(getBranchRiskLevel('main', pairs)).toBe('none')
    })
  })

  describe('sortPairsByScoreDesc', () => {
    it('sorts descending by score', () => {
      const pairs: PairRisk[] = [
        { ...mockPair, score: 30 },
        { ...mockPair, score: 60 },
        { ...mockPair, score: 90 },
      ]
      const sorted = sortPairsByScoreDesc(pairs)
      expect(sorted[0].score).toBe(90)
      expect(sorted[1].score).toBe(60)
      expect(sorted[2].score).toBe(30)
    })
    it('does not mutate original array', () => {
      const pairs: PairRisk[] = [
        { ...mockPair, score: 30 },
        { ...mockPair, score: 60 },
      ]
      const original = [...pairs]
      sortPairsByScoreDesc(pairs)
      expect(pairs).toEqual(original)
    })
  })
})