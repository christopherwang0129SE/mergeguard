import type { PairRisk } from '../types';
import { pairKey } from '../utils/run';
import { VERDICT_TEXT, pairVerdict, rankPairs } from '../utils/verdict';

interface PairIndexProps {
  pairs: PairRisk[];
  selectedKey: string | null;
  onSelect: (pair: PairRisk) => void;
  /** Scores are provisional while the stream is still running. */
  live?: boolean;
  /** Branches announced by the stream that have no scored pair yet. */
  pendingCount?: number;
}

/**
 * Every branch pair, worst first: score, the two branches and the verdict.
 * Choosing one opens its detail.
 */
export const PairIndex = ({ pairs, selectedKey, onSelect, live = false, pendingCount = 0 }: PairIndexProps) => {
  const ranked = rankPairs(pairs);
  return (
    <nav className="index" aria-labelledby="index-heading" data-testid="pair-index">
      <h2 id="index-heading" className="index-title">
        Branch pairs <span className="index-count tabular">{pairs.length}</span>
        {live && <span className="index-live">scoring</span>}
      </h2>
      {ranked.length === 0 && !live && <p className="muted">No branch pairs.</p>}
      <ol className="index-list">
        {ranked.map((pair) => {
          const key = pairKey(pair);
          const verdict = pairVerdict(pair);
          const selected = key === selectedKey;
          return (
            <li key={key}>
              <button
                type="button"
                className="index-row"
                data-level={pair.level}
                data-verdict={live && verdict === 'clean' ? 'pending' : verdict}
                aria-current={selected ? 'true' : undefined}
                onClick={() => onSelect(pair)}
                data-testid="pair-row"
              >
                <span className="row-score tabular" data-testid="pair-score">
                  {Math.round(pair.score)}
                </span>
                <span className="row-branches">
                  <span className="row-branch">{pair.branch_a}</span>
                  <span className="row-branch">
                    <span className="sr-only">and </span>
                    {pair.branch_b}
                  </span>
                </span>
                <span className="row-verdict">{live && verdict === 'clean' ? 'Awaiting verdict' : VERDICT_TEXT[verdict]}</span>
              </button>
            </li>
          );
        })}
        {live &&
          Array.from({ length: pendingCount }, (_, i) => (
            <li key={`pending-${i}`} className="index-pending" aria-hidden="true">
              <span className="skeleton"></span>
            </li>
          ))}
      </ol>
    </nav>
  );
};
