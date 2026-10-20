import type { Verification, VerifyRun } from '../types';

/**
 * Runs counted over the kept generated tests only. A generated test that
 * failed on its own branch was discarded by the backend, so counting it
 * would show a misleading failure. Kept = passed on the branch it was written
 * for (the `a` or `b` run). Runs without per-case data are returned as is.
 */
export const keptRuns = (verification: Verification): VerifyRun[] => {
  const runs = verification.runs;
  if (!runs.some((run) => run.cases)) return runs;
  const kept = new Set<string>();
  for (const run of runs) {
    if (run.target !== 'a' && run.target !== 'b') continue;
    for (const [name, status] of Object.entries(run.cases ?? {})) if (status === 'passed') kept.add(name);
  }
  for (const name of verification.confirming_tests ?? []) kept.add(name);
  return runs.map((run) => {
    if (!run.cases) return run;
    const statuses = Object.entries(run.cases)
      .filter(([name]) => kept.has(name))
      .map(([, status]) => status);
    const passed = statuses.filter((s) => s === 'passed').length;
    return { ...run, passed, failed: statuses.length - passed, errors: 0 };
  });
};
