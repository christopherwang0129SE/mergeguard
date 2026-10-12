import { getBranchRiskLevel, levelToColor, levelToText } from '../utils/risk';
import type { PairRisk, RiskLevel } from '../types';

interface BranchLightsProps {
  branches: string[];
  pairs: PairRisk[];
}

export const BranchLights = ({ branches, pairs }: BranchLightsProps) => {
  if (branches.length === 0) {
    return (
      <div className="branch-lights">
        <h2>Branch Risk Lights</h2>
        <p className="empty-state">No branches to display.</p>
      </div>
    );
  }

  return (
    <div className="branch-lights">
      <h2>Branch Risk Lights</h2>
      <div className="lights">
        {branches.map((branch) => {
          const level: RiskLevel = getBranchRiskLevel(branch, pairs);
          return (
            <div key={branch} className="light">
              <div
                className="light-circle"
                style={{ backgroundColor: levelToColor(level) }}
                aria-hidden="true"
              ></div>
              <div className="light-label">
                {branch}: {levelToText(level)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
