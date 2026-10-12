import type { BranchTestRun, BranchTests } from '../types';

interface BranchTestDetailsProps {
  branchTests: BranchTests;
}

const BADGE: Record<BranchTests['status'], { label: string; className: string }> = {
  confirmed: {
    label: 'Confirmed by branch test (existing tests, no LLM)',
    className: 'badge badge-confirmed badge-branch-test',
  },
  passed: { label: 'Branch tests pass on merge', className: 'badge badge-source' },
  skipped: { label: 'Branch tests skipped', className: 'badge badge-source' },
  unsupported: { label: 'Branch tests unsupported', className: 'badge badge-source' },
  error: { label: 'Branch tests error', className: 'badge badge-suspected' },
};

const TARGET_LABEL: Record<BranchTestRun['target'], string> = {
  a: 'Branch A',
  b: 'Branch B',
  base: 'Merge-base',
  merge: 'Merge A+B',
  'merge-rerun': 'Merge A+B (rerun)',
};

const runSummary = (run: BranchTestRun): string => {
  if (run.timed_out) {
    return 'timed out';
  }
  if (run.problem && run.passed + run.failed + run.errors === 0) {
    return `no results: ${run.problem}`;
  }
  const parts = [`${run.passed} passed`];
  if (run.failed > 0) parts.push(`${run.failed} failed`);
  if (run.errors > 0) parts.push(`${run.errors} errors`);
  return parts.join(', ');
};

/**
 * Deterministic "branch tests on merge" result: the test files each branch
 * added or modified, run on its own branch and on the A+B merge. Kept
 * visually separate from the Nemotron intent test.
 */
export const BranchTestDetails = ({ branchTests }: BranchTestDetailsProps) => {
  const badge = BADGE[branchTests.status];
  return (
    <div className="verification branch-tests" data-testid="branch-tests">
      <span className={badge.className} title={branchTests.reason}>
        {badge.label}
      </span>
      <div className="verification-reason">{branchTests.reason}</div>
      {branchTests.confirming.length > 0 && (
        <ul className="verification-confirming" aria-label="Branch tests failing on merge">
          {branchTests.confirming.map((failure) => (
            <li key={failure.test_id}>
              <code>{failure.test_id}</code> ({failure.branch}&apos;s test)
              {failure.excerpt ? `: ${failure.excerpt}` : ''}
            </li>
          ))}
        </ul>
      )}
      {branchTests.runs.length > 0 && (
        <details className="verification-details">
          <summary>
            Branch test runs ({branchTests.duration_s.toFixed(1)}s)
          </summary>
          <ul className="verification-runs">
            {branchTests.runs.map((run, index) => (
              <li key={`${run.target}-${run.runner ?? 'pytest'}-${run.package ?? ''}-${index}`}>
                <strong>{TARGET_LABEL[run.target]}</strong> ({run.ref}
                {run.runner && run.runner !== 'pytest'
                  ? `, ${run.runner} in ${run.package || '.'}`
                  : ''}
                , {run.files.length} file
                {run.files.length === 1 ? '' : 's'}): {runSummary(run)}
              </li>
            ))}
          </ul>
          {(branchTests.setup?.length ?? 0) > 0 && (
            <div className="verification-sandbox" data-testid="branch-test-setup">
              JS setup
              {(branchTests.install_s ?? 0) > 0 || (branchTests.build_s ?? 0) > 0
                ? ` (install ${(branchTests.install_s ?? 0).toFixed(1)}s, builds ${(branchTests.build_s ?? 0).toFixed(1)}s)`
                : ''}
              : {branchTests.setup?.join('; ')}
            </div>
          )}
          {branchTests.sandbox && (
            <div className="verification-sandbox">Sandbox: {branchTests.sandbox}</div>
          )}
        </details>
      )}
    </div>
  );
};
