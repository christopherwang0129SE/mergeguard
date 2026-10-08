import type { RiskLevel, PairRisk } from '../types';

export const levelToColor = (level: RiskLevel): string => {
  switch (level) {
    case 'none':
    case 'low':
      return '#10b981'; // green-500
    case 'medium':
      return '#f59e0b'; // amber-500
    case 'high':
      return '#ef4444'; // red-500
    default:
      return '#6b7280'; // gray-500
  }
};

/**
 * Text color for the risk level word. Darker than the dot colors so the
 * word keeps enough contrast on white (spec: #B91C1C / #B45309 / #047857).
 */
export const levelToTextColor = (level: RiskLevel): string => {
  switch (level) {
    case 'none':
    case 'low':
      return '#047857'; // green-700
    case 'medium':
      return '#b45309'; // amber-700
    case 'high':
      return '#b91c1c'; // red-700
    default:
      return '#4b5563'; // gray-600
  }
};

export const levelToText = (level: RiskLevel): string => {
  switch (level) {
    case 'none':
      return 'None';
    case 'low':
      return 'Low';
    case 'medium':
      return 'Medium';
    case 'high':
      return 'High';
    default:
      return 'Unknown';
  }
};

/**
 * Numeric order of risk levels: none < low < medium < high.
 */
export const levelRank = (level: RiskLevel): number => {
  switch (level) {
    case 'none':
      return 0;
    case 'low':
      return 1;
    case 'medium':
      return 2;
    case 'high':
      return 3;
    default:
      return -1;
  }
};

/**
 * Git risk level from a 0-100 score, using the same thresholds as the
 * backend (backend/app/services/sentinel.py `_level`):
 * >=70 high, >=40 medium, >0 low, otherwise none.
 */
export const gitLevelFromScore = (score: number | null | undefined): RiskLevel => {
  if (score === null || score === undefined) {
    return 'none';
  }
  if (score >= 70) {
    return 'high';
  }
  if (score >= 40) {
    return 'medium';
  }
  if (score > 0) {
    return 'low';
  }
  return 'none';
};

/**
 * The floor rule: when the branches' changed regions actually overlap
 * (`overlapping_files > 0`) and the git heuristic outscored the model
 * (`git_score > model_score`), the published score is floored at the git
 * score. The UI then explains that the model's lower assessment was kept
 * at the git level.
 */
export const isFloorRule = (pair: {
  overlapping_files?: number;
  git_score?: number | null;
  model_score?: number | null;
}): boolean =>
  (pair.overlapping_files ?? 0) > 0 &&
  pair.git_score !== null &&
  pair.git_score !== undefined &&
  pair.model_score !== null &&
  pair.model_score !== undefined &&
  pair.git_score > pair.model_score;

/**
 * A hidden conflict: the published score came from the model
 * (`score_source === 'model'`), the model's level is medium or high, and
 * it is strictly higher than the git level derived from `git_score` — the
 * model flagged a semantic clash the diff heuristic missed. A none -> low
 * move is not a hidden conflict: the model still sees no real risk.
 */
export const isHiddenConflict = (pair: {
  score_source?: 'model' | 'git';
  model_level?: RiskLevel | null;
  git_score?: number | null;
}): boolean =>
  pair.score_source === 'model' &&
  pair.model_level !== null &&
  pair.model_level !== undefined &&
  (pair.model_level === 'medium' || pair.model_level === 'high') &&
  levelRank(pair.model_level) > levelRank(gitLevelFromScore(pair.git_score));

/**
 * A disagreement for the summary bar: the published score came from the
 * model, the model's level differs from the git level, and at least one
 * side is medium or high. A none <-> low move is not a disagreement —
 * both sides agree there is no real risk.
 */
export const isDisagree = (pair: {
  score_source?: 'model' | 'git';
  model_level?: RiskLevel | null;
  git_score?: number | null;
}): boolean => {
  if (pair.score_source !== 'model') {
    return false;
  }
  if (pair.model_level === null || pair.model_level === undefined) {
    return false;
  }
  const gitLevel = gitLevelFromScore(pair.git_score);
  if (pair.model_level === gitLevel) {
    return false;
  }
  return (
    pair.model_level === 'medium' ||
    pair.model_level === 'high' ||
    gitLevel === 'medium' ||
    gitLevel === 'high'
  );
};

export const getBranchRiskLevel = (
  branch: string,
  pairs: PairRisk[]
): RiskLevel => {
  const relevantPairs = pairs.filter(
    (pair) => pair.branch_a === branch || pair.branch_b === branch
  );

  if (relevantPairs.length === 0) {
    return 'none';
  }

  const levels: RiskLevel[] = relevantPairs.map((pair) => pair.level);
  const maxLevel = levels.reduce((max, level) =>
    levelRank(level) > levelRank(max) ? level : max
  , 'none');

  return maxLevel;
};

export const sortPairsByScoreDesc = (pairs: PairRisk[]): PairRisk[] => {
  return [...pairs].sort((a, b) => b.score - a.score);
};
