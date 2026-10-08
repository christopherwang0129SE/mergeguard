import { levelToColor, levelToText } from '../utils/risk';
import { sortPairsByScoreDesc } from '../utils/risk';
import type { PairRisk } from '../types';

interface PairTableProps {
  pairs: PairRisk[];
  risk_model: string | null;
}

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
        <p>No branch pairs to display.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Branch A</th>
              <th>Branch B</th>
              <th>Score</th>
              <th>Level</th>
              <th>Score Source</th>
              <th>Rationale</th>
              {pairs[0]?.fallback_reason !== undefined && (
                <th>Fallback Reason</th>
              )}
            </tr>
          </thead>
          <tbody>
            {sortedPairs.map((pair) => (
              <tr key={`${pair.branch_a}-${pair.branch_b}`}>
                <td>{pair.branch_a}</td>
                <td>{pair.branch_b}</td>
                <td>{pair.score.toFixed(1)}</td>
                <td>
                  <span
                    style={{
                      display: 'inline-block',
                      width: '12px',
                      height: '12px',
                      backgroundColor: levelToColor(pair.level),
                      marginRight: '8px',
                    }}
                  ></span>
                  {levelToText(pair.level)}
                </td>
                <td>{pair.score_source}</td>
                <td>{pair.rationale}</td>
                {pair.fallback_reason !== undefined && (
                  <td>{pair.fallback_reason ?? '-'}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};