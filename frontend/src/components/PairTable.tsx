import {
  levelToColor,
  levelToTextColor,
  levelToText,
  sortPairsByScoreDesc,
  isFloorRule,
  isHiddenConflict,
} from '../utils/risk';
import type { PairRisk } from '../types';
import { VerificationDetails } from './VerificationDetails';

interface PairTableProps {
  pairs: PairRisk[];
  risk_model: string | null;
}

const formatScore = (score: number | null | undefined): string =>
  score === null || score === undefined ? '—' : String(Math.round(score));

/**
 * Two-line rationale for the floor rule: the model's assessment plus why
 * the git level was kept. File names come from the overlapping collisions.
 */
const floorRationale = (pair: PairRisk): [string, string] => {
  const modelLine =
    pair.model_level === 'medium' || pair.model_level === 'high'
      ? `Nemotron: ${pair.model_level} risk (${formatScore(pair.model_score)}/100).`
      : `Nemotron: no conflict expected (${formatScore(pair.model_score)}/100).`;
  const files = pair.collisions
    .filter((c) => c.overlapping)
    .map((c) => c.path);
  const touched = files.length > 0 ? files.join(', ') : `${pair.overlapping_files} files`;
  const floorLine = `Floor rule: both branches touch ${touched}, so kept ${levelToText(pair.level)}.`;
  return [modelLine, floorLine];
};

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
              <th>Level</th>
              <th>Git Score</th>
              <th>Model Score</th>
              <th>Source</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {sortedPairs.map((pair) => {
              const floor = isFloorRule(pair);
              const hidden = isHiddenConflict(pair);
              const confirmed = pair.verification?.status === 'confirmed';
              const [modelLine, floorLine] = floor ? floorRationale(pair) : ['', ''];
              const rowClass = confirmed
                ? 'row-confirmed'
                : hidden
                  ? 'row-hidden-conflict'
                  : undefined;
              return (
                <tr
                  key={`${pair.branch_a}-${pair.branch_b}`}
                  className={rowClass}
                >
                  <td className="branch-name">{pair.branch_a}</td>
                  <td className="branch-name">{pair.branch_b}</td>
                  <td className="level-cell">
                    <div className="level-top">
                      <span
                        className="level-dot"
                        style={{ backgroundColor: levelToColor(pair.level) }}
                        aria-hidden="true"
                      ></span>
                      <span className="risk-number">
                        Risk {Math.round(pair.score)} / 100
                      </span>
                    </div>
                    <span className="level-word" style={{ color: levelToTextColor(pair.level) }}>
                      {levelToText(pair.level)}
                    </span>
                    {pair.score_note && (
                      <span className="score-note" data-testid="score-note">
                        {pair.score_note}
                        {pair.blended_score !== null && pair.blended_score !== undefined
                          ? ` (blend ${Math.round(pair.blended_score)})`
                          : ''}
                      </span>
                    )}
                  </td>
                  <td>{formatScore(pair.git_score)}</td>
                  <td>{formatScore(pair.model_score)}</td>
                  <td>
                    {pair.score_source === 'model' ? (
                      <span className="badge badge-source">Nemotron</span>
                    ) : (
                      <span className="badge badge-source">
                        Git fallback
                        {pair.fallback_reason ? (
                          <span className="fallback-reason"> · {pair.fallback_reason}</span>
                        ) : null}
                      </span>
                    )}
                  </td>
                  <td className="rationale-cell">
                    {floor ? (
                      <div className="rationale-lines">
                        <span className="badge badge-floor">
                          Floor rule: {pair.overlapping_files} shared files
                        </span>
                        <div>{modelLine}</div>
                        <div>{floorLine}</div>
                      </div>
                    ) : hidden ? (
                      <div className="rationale-lines">
                        <span className="badge badge-hidden">
                          Nemotron flags hidden conflict
                        </span>
                        <div>{pair.model_rationale ?? pair.rationale}</div>
                      </div>
                    ) : (
                      pair.rationale
                    )}
                    {pair.static_breaks && pair.static_breaks.length > 0 && (
                      <ul className="static-breaks" aria-label="Static signature check">
                        {pair.static_breaks.map((line) => (
                          <li key={line}>Static check: {line}</li>
                        ))}
                      </ul>
                    )}
                    {pair.unsupported_claims && pair.unsupported_claims.length > 0 && (
                      <div className="unsupported-claims">
                        Not supported by the static checks:{' '}
                        {pair.unsupported_claims.join(' ')}
                      </div>
                    )}
                    {pair.verification && (
                      <VerificationDetails verification={pair.verification} />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
};
