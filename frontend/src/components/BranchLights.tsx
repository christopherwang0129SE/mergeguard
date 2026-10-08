import { levelToColor, levelToText } from '../utils/risk';

interface BranchLightsProps {
  branches: string[];
  pairs: any[]; // PairRisk[]
}

export const BranchLights = ({ branches, pairs }: BranchLightsProps) => {
  // Compute risk level per branch
  const branchRisk: Record<string, string> = {};
  branches.forEach((branch) => {
    // We'll compute using a helper function, but for simplicity we can do it here.
    // Since we don't have the utils in the component, we'll compute directly.
    // Better to import the function, but let's keep it simple and compute.
    // We'll create a set of pairs for this branch.
    const relevantPairs = pairs.filter(
      (pair) => pair.branch_a === branch || pair.branch_b === branch
    );
    if (relevantPairs.length === 0) {
      branchRisk[branch] = 'none';
    } else {
      const levels = relevantPairs.map((pair) => pair.level);
      const order: Record<string, number> = { none: 0, low: 1, medium: 2, high: 3 };
      const maxLevel = levels.reduce((max, level) =>
        order[level] > order[max] ? level : max
      , 'none');
      branchRisk[branch] = maxLevel;
    }
  });

  return (
    <div className="branch-lights">
      <h2>Branch Risk Lights</h2>
      <div className="lights">
        {branches.map((branch) => (
          <div key={branch} className="light">
            <div
              className="light-circle"
              style={{ backgroundColor: levelToColor(branchRisk[branch] as any) }}
            ></div>
            <div className="light-label">
              {branch}: {levelToText(branchRisk[branch] as any)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};