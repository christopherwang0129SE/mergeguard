import { describe, it, expect } from 'vitest'
import {
  levelToColor,
  levelToTextColor,
  levelToText,
  getBranchRiskLevel,
  sortPairsByScoreDesc,
  gitLevelFromScore,
  isFloorRule,
  isHiddenConflict,
  levelRank,
} from './risk'
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

  describe('levelToTextColor', () => {
    it('uses darker text colors for the level word', () => {
      expect(levelToTextColor('none')).toBe('#047857')
      expect(levelToTextColor('low')).toBe('#047857')
      expect(levelToTextColor('medium')).toBe('#b45309')
      expect(levelToTextColor('high')).toBe('#b91c1c')
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

  describe('gitLevelFromScore', () => {
    // Same thresholds as the backend (backend/app/services/sentinel.py _level):
    // >=70 high, >=40 medium, >0 low, otherwise none.
    it('maps scores to the backend thresholds', () => {
      expect(gitLevelFromScore(0)).toBe('none')
      expect(gitLevelFromScore(0.1)).toBe('low')
      expect(gitLevelFromScore(39.9)).toBe('low')
      expect(gitLevelFromScore(40)).toBe('medium')
      expect(gitLevelFromScore(69.9)).toBe('medium')
      expect(gitLevelFromScore(70)).toBe('high')
      expect(gitLevelFromScore(100)).toBe('high')
    })
  })

  describe('levelRank', () => {
    it('orders none < low < medium < high', () => {
      expect(levelRank('none')).toBeLessThan(levelRank('low'))
      expect(levelRank('low')).toBeLessThan(levelRank('medium'))
      expect(levelRank('medium')).toBeLessThan(levelRank('high'))
    })
  })

  describe('isFloorRule', () => {
    it('is true when overlapping files exist and git outscored the model', () => {
      expect(
        isFloorRule({ overlapping_files: 2, git_score: 80, model_score: 40 })
      ).toBe(true)
    })
    it('is false when there are no overlapping files', () => {
      expect(
        isFloorRule({ overlapping_files: 0, git_score: 80, model_score: 40 })
      ).toBe(false)
    })
    it('is false when the model score is not below the git score', () => {
      expect(
        isFloorRule({ overlapping_files: 1, git_score: 40, model_score: 80 })
      ).toBe(false)
      expect(
        isFloorRule({ overlapping_files: 1, git_score: 40, model_score: 40 })
      ).toBe(false)
    })
    it('is false when the model score is missing', () => {
      expect(
        isFloorRule({ overlapping_files: 1, git_score: 40, model_score: null })
      ).toBe(false)
    })
  })

  describe('isHiddenConflict', () => {
    it('is true when the model source is used and the model level exceeds the git level', () => {
      expect(
        isHiddenConflict({
          score_source: 'model',
          model_level: 'high',
          git_score: 10,
        })
      ).toBe(true)
      expect(
        isHiddenConflict({
          score_source: 'model',
          model_level: 'medium',
          git_score: 10,
        })
      ).toBe(true)
    })
    it('is false when the model level does not exceed the git level', () => {
      expect(
        isHiddenConflict({
          score_source: 'model',
          model_level: 'low',
          git_score: 50,
        })
      ).toBe(false)
      expect(
        isHiddenConflict({
          score_source: 'model',
          model_level: 'medium',
          git_score: 50,
        })
      ).toBe(false)
    })
    it('is false when the published score came from git', () => {
      expect(
        isHiddenConflict({
          score_source: 'git',
          model_level: 'high',
          git_score: 10,
        })
      ).toBe(false)
    })
    it('is false when the model level is missing', () => {
      expect(
        isHiddenConflict({
          score_source: 'model',
          model_level: null,
          git_score: 10,
        })
      ).toBe(false)
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
