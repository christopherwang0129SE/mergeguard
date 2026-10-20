export interface TruthRun {
  target: string;
  ref?: string;
  passed: number;
  failed: number;
  errors: number;
  timed_out: boolean;
  problem?: string;
}

interface TruthTableProps {
  runs: TruthRun[];
  branchA: string;
  branchB: string;
  /** Accessible name; a pair can show two tables. */
  label: string;
}

type Outcome = 'pass' | 'fail' | 'none' | 'missing';

const outcomeOf = (run: TruthRun | undefined): { state: Outcome; word: string; counts: string } => {
  if (!run) return { state: 'missing', word: 'not run', counts: '' };
  if (run.timed_out) return { state: 'fail', word: 'timeout', counts: '' };
  const bad = run.failed + run.errors;
  const counts = [run.passed > 0 && `${run.passed} passed`, bad > 0 && `${bad} failed`].filter(Boolean).join(', ');
  if (bad > 0) return { state: 'fail', word: 'fail', counts };
  if (run.passed > 0) return { state: 'pass', word: 'pass', counts };
  return { state: 'none', word: 'no result', counts: run.problem ?? 'no tests ran' };
};

/** The last run per target wins (a merge rerun replaces the first merge run). */
const byTarget = (runs: TruthRun[]) => {
  const map = new Map<string, TruthRun>();
  for (const run of runs) map.set(run.target === 'merge-rerun' ? 'merge' : run.target, run);
  return map;
};

/**
 * The same tests on the four checkouts of a merge, as a truth table:
 * columns add branch B, rows add branch A. A semantic conflict reads
 * pass / pass on the single branches and fail where both meet.
 */
export const TruthTable = ({ runs, branchA, branchB, label }: TruthTableProps) => {
  const map = byTarget(runs);
  if (map.size === 0) return null;
  const cell = (target: 'base' | 'a' | 'b' | 'merge', name: string) => {
    const o = outcomeOf(map.get(target));
    return (
      <td className="tt-cell" data-outcome={o.state} data-target={target} data-testid={`tt-${target}`}>
        <span className="tt-name" title={name}>
          {name}
        </span>
        <span className="tt-word">{o.word}</span>
        {o.counts && <span className="tt-counts tabular">{o.counts}</span>}
      </td>
    );
  };
  return (
    <table className="truth" aria-label={label}>
      <thead>
        <tr>
          <td className="tt-corner"></td>
          <th scope="col">
            without <span className="tt-branch">B</span>
          </th>
          <th scope="col">
            with <span className="tt-branch">B</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <th scope="row">
            without <span className="tt-branch">A</span>
          </th>
          {cell('base', 'base')}
          {cell('b', branchB)}
        </tr>
        <tr>
          <th scope="row">
            with <span className="tt-branch">A</span>
          </th>
          {cell('a', branchA)}
          {cell('merge', 'A + B merged')}
        </tr>
      </tbody>
    </table>
  );
};
