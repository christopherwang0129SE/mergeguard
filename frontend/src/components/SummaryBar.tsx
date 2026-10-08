import { levelToColor, levelToText, levelRank, gitLevelFromScore } from '../utils/risk';
import type { PairRisk, RiskLevel } from '../types';

interface SummaryBarProps {
  pairs: PairRisk[];
}

/**
 * Worst published level across all pairs.
 */
const worstLevel = (pairs: PairRisk[]): RiskLevel =>
  pairs.reduce<RiskLevel>(
    (worst, pair) => (levelRank(pair.level) > levelRank(worst) ? pair.level : worst),
    'none'
  );

/**
 * Pairs where Nemotron disagrees with git: the published score came from
 * the model and the model's level differs from the git level.
 */
const disagreeCount = (pairs: PairRisk[]): number =>
  pairs.filter(
    (pair) =>
      pair.score_source === 'model' &&
      pair.model_level !== null &&
      pair.model_level !== undefined &&
      pair.model_level !== gitLevelFromScore(pair.git_score)
  ).length;

/**
 * Pairs where the model was actually used (published score from the model).
 */
const modelUsedCount = (pairs: PairRisk[]): number =>
  pairs.filter((pair) => pair.score_source === 'model').length;

export const SummaryBar = ({ pairs }: SummaryBarProps) => {
  const total = pairs.length;
  const worst = worstLevel(pairs);
  // Pairs sitting at the worst published level, e.g. "1 of 3 branch pairs at
  // medium risk" when one of three pairs is medium (the worst).
  const atWorst = pairs.filter((pair) => pair.level === worst).length;
  const risky = pairs.filter(
    (pair) => pair.level === 'medium' || pair.level === 'high'
  ).length;
  const disagree = disagreeCount(pairs);
  const modelUsed = modelUsedCount(pairs);

  const line1 =
    risky > 0
      ? `${atWorst} of ${total} branch pairs at ${levelToText(worst).toLowerCase()} risk`
      : `No risky pairs among ${total}`;

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
