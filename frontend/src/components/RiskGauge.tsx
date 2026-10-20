import type { PairRisk } from '../types';
import { levelToText } from '../utils/risk';

interface RiskGaugeProps {
  pair: PairRisk;
}

const TICKS = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
const clamp = (n: number) => Math.min(100, Math.max(0, n));

/**
 * The risk score as an instrument: the published score in large numerals,
 * read off a 0-100 scale whose zones match the backend's thresholds
 * (40 medium, 70 high). The git heuristic and Nemotron's own score sit on
 * the same scale, so a disagreement between them is visible at a glance.
 */
export const RiskGauge = ({ pair }: RiskGaugeProps) => {
  const score = Math.round(pair.score);
  const marks: Array<{ key: string; label: string; value: number }> = [];
  if (pair.git_score !== null && pair.git_score !== undefined) {
    marks.push({ key: 'git', label: 'git', value: Math.round(pair.git_score) });
  }
  if (pair.model_score !== null && pair.model_score !== undefined) {
    marks.push({ key: 'model', label: 'Nemotron', value: Math.round(pair.model_score) });
  }
  // Two markers on the same value would print on top of each other.
  const collide = marks.length === 2 && Math.abs(marks[0].value - marks[1].value) < 12;

  return (
    <figure className="gauge" data-level={pair.level} data-testid="risk-gauge">
      <div className="gauge-readout">
        <span className="gauge-num tabular" data-testid="gauge-score">
          {score}
        </span>
        <span className="gauge-unit">
          <span className="gauge-level">{levelToText(pair.level)} risk</span>
          <span className="gauge-max">out of 100</span>
        </span>
      </div>
      <div className="gauge-scale" aria-hidden="true">
        <div className="gauge-zones">
          <span className="zone zone-low"></span>
          <span className="zone zone-medium"></span>
          <span className="zone zone-high"></span>
        </div>
        <div className="gauge-ticks">
          {TICKS.map((t) => (
            <span key={t} className="tick" data-major={t % 50 === 0 || t === 40 || t === 70 || undefined} style={{ left: `${t}%` }}></span>
          ))}
        </div>
        <span className="needle" style={{ left: `${clamp(score)}%` }}></span>
        {marks.map((m, i) => (
          <span
            key={m.key}
            className="mark"
            data-kind={m.key}
            data-low={collide && i === 1 ? true : undefined}
            data-align={m.value < 12 ? 'start' : m.value > 88 ? 'end' : undefined}
            style={{ left: `${clamp(m.value)}%` }}
          >
            <span className="mark-label">
              {m.label} <span className="tabular">{m.value}</span>
            </span>
          </span>
        ))}
        <div className="gauge-legend">
          <span style={{ left: '0%' }}>0</span>
          <span style={{ left: '40%' }}>40</span>
          <span style={{ left: '70%' }}>70</span>
          <span style={{ left: '100%' }}>100</span>
        </div>
      </div>
      <figcaption className="sr-only">
        Risk score {score} out of 100, {levelToText(pair.level).toLowerCase()} risk.
        {marks.map((m) => ` ${m.label} scored ${m.value}.`).join('')}
      </figcaption>
    </figure>
  );
};
