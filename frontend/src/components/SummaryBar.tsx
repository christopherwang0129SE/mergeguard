import {
  levelToColor,
  levelToText,
  levelRank,
  isDisagree,
  isHiddenConflict,
  effectivePairLevel,
} from '../utils/risk';
import type { PairRisk, RiskLevel } from '../types';

interface SummaryBarProps {
  pairs: PairRisk[];
}

/**
 * Worst effective level across all pairs: the published level, raised to the
 * model's level for hidden conflicts (the model flagged a medium/high
 * semantic clash the git heuristic missed).
 */
const worstLevel = (pairs: PairRisk[]): RiskLevel =>
  pairs.reduce<RiskLevel>(
    (worst, pair) =>
      levelRank(effectivePairLevel(pair)) > levelRank(worst)
        ? effectivePairLevel(pair)
        : worst,
    'none'
  );

/**
 * Pairs where Nemotron disagrees with git: the published score came from
 * the model, the levels differ, and at least one side is medium or high
 * (a none <-> low move is not a disagreement).
 */
const disagreeCount = (pairs: PairRisk[]): number =>
  pairs.filter((pair) => isDisagree(pair)).length;

/**
 * Pairs where the model was actually used (published score from the model).
 */
const modelUsedCount = (pairs: PairRisk[]): number =>
  pairs.filter((pair) => pair.score_source === 'model').length;

export const SummaryBar = ({ pairs }: SummaryBarProps) => {
  const total = pairs.length;
  const worst = worstLevel(pairs);
  const hidden = pairs.filter((pair) => isHiddenConflict(pair)).length;
  // Pairs whose effective level is medium or high: shown medium/high pairs
  // plus hidden conflicts.
  const risky = pairs.filter(
    (pair) =>
      levelRank(effectivePairLevel(pair)) >= levelRank('medium')
  ).length;
  // Pairs sitting at the worst effective level, e.g. "1 of 3 branch pairs at
  // medium risk" when one of three pairs is medium (the worst).
  const atWorst = pairs.filter(
    (pair) => effectivePairLevel(pair) === worst
  ).length;
  const disagree = disagreeCount(pairs);
  const modelUsed = modelUsedCount(pairs);

  let line1: string;
  if (risky === 0) {
    line1 = `No risky pairs among ${total}`;
  } else if (hidden > 0 && !pairs.some((pair) => pair.level === 'medium' || pair.level === 'high')) {
    // Only hidden conflicts, nothing published medium/high.
    line1 = `${hidden} hidden conflict${hidden === 1 ? '' : 's'} among ${total} pairs`;
  } else {
    const suffix = hidden > 0 ? ` · ${hidden} hidden` : '';
    line1 = `${atWorst} of ${total} branch pairs at ${levelToText(worst).toLowerCase()} risk${suffix}`;
  }

  let line2: string;
  if (modelUsed === 0) {
    line2 = 'Git only: Nemotron unavailable';
  } else if (disagree > 0) {
    line2 = `Nemotron disagrees on ${disagree} pair(s)`;
  } else {
    line2 = 'Nemotron agrees with git on all pairs';
  }

  return (
    <div
      className="summary-bar"
      style={{ borderLeftColor: levelToColor(worst) }}
      role="status"
      aria-label="Analysis summary"
    >
      <div className="summary-line1">{line1}</div>
      <div className="summary-line2">{line2}</div>
    </div>
  );
};
