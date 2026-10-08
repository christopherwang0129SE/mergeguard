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
  // Order: none < low < medium < high
  const levelOrder: Record<RiskLevel, number> = {
    none: 0,
    low: 1,
    medium: 2,
    high: 3,
  };

  const maxLevel = levels.reduce((max, level) =>
    levelOrder[level] > levelOrder[max] ? level : max
  , 'none');

  return maxLevel;
};

export const sortPairsByScoreDesc = (pairs: PairRisk[]): PairRisk[] => {
  return [...pairs].sort((a, b) => b.score - a.score);
};