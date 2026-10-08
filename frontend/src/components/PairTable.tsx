import { levelToColor, levelToText, sortPairsByScoreDesc } from '../utils/risk';
import type { PairRisk } from '../types';

interface PairTableProps {
  pairs: PairRisk[];
  risk_model: string | null;
}

const formatScore = (score: number | null | undefined): string =>
  score === null || score === undefined ? '—' : String(Math.round(score));

export const PairTable = ({ pairs, risk_model }: PairTableProps) => {
  const sortedPairs = sortPairsByScoreDesc(pairs);

  return (
    <div className="pair-table">
      <h2>Branch Pairs Risk</h2>
      {risk_model && (
        <div className="risk-model">
          Risk model in use: {risk_model}
        </div>
      )}
      {sortedPairs.length === 0 ? (
        <p className="empty-state">No branch pairs to display.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Branch A</th>
              <th>Branch B</th>
              <th>Score</th>
              <th>Level</th>
              <th>Git Score</th>
              <th>Model Score</th>
              <th>Source</th>
              <th>Fallback</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {sortedPairs.map((pair) => (
              <tr key={`${pair.branch_a}-${pair.branch_b}`}>
                <td>{pair.branch_a}</td>
                <td>{pair.branch_b}</td>
                <td>{Math.round(pair.score)}</td>
                <td>
                  <span
                    className="level-dot"
                    style={{ backgroundColor: levelToColor(pair.level) }}
                    aria-hidden="true"
                  ></span>
                  {levelToText(pair.level)}
                </td>
                <td>{formatScore(pair.git_score)}</td>
                <td>{formatScore(pair.model_score)}</td>
                <td>
                  <span className={`badge badge-${pair.score_source}`}>
                    {pair.score_source}
                  </span>
                </td>
                <td>
                  {pair.fallback_reason ? (
                    <span className="badge badge-fallback">{pair.fallback_reason}</span>
                  ) : (
                    '—'
                  )}
                </td>
                <td>{pair.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};
