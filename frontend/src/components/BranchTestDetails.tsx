import type { BranchTestRun, BranchTests } from '../types';
import { CodeBlock } from './CodeBlock';
import { TruthTable } from './TruthTable';

interface BranchTestDetailsProps {
  branchTests: BranchTests;
  branchA: string;
  branchB: string;
}

const STATUS: Record<BranchTests['status'], { label: string; tone: 'fail' | 'pass' | 'neutral' | 'warn' }> = {
  confirmed: { label: 'Confirmed by branch test', tone: 'fail' },
  passed: { label: 'Branch tests pass on merge', tone: 'pass' },
  skipped: { label: 'Branch tests skipped', tone: 'neutral' },
  unsupported: { label: 'Branch tests unsupported', tone: 'neutral' },
  error: { label: 'Branch tests error', tone: 'warn' },
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
 * added or modified, run on its own branch and on the A+B merge. No LLM.
 */
export const BranchTestDetails = ({ branchTests, branchA, branchB }: BranchTestDetailsProps) => {
  const status = STATUS[branchTests.status];
  const confirmed = branchTests.status === 'confirmed';
  return (
    <section className="block" data-status={branchTests.status} data-testid="branch-tests" aria-labelledby="branch-tests-heading">
      <h3 id="branch-tests-heading" className="block-title">
        Branch tests <span className="block-sub">each branch&apos;s own tests on the merge, no LLM</span>
      </h3>
      <div className="block-body">
        <p className="status-line">
          <span className="status" data-tone={status.tone}>
            {status.label}
          </span>
          {!confirmed && <span className="status-reason">{branchTests.reason}</span>}
        </p>
        {branchTests.runs.length > 0 && (
          <TruthTable runs={branchTests.runs} branchA={branchA} branchB={branchB} label="Branch test results per checkout" />
        )}
        {branchTests.confirming.length > 0 && (
          <ul className="failures" aria-label="Branch tests failing on merge">
            {branchTests.confirming.map((failure) => (
              <li key={failure.test_id}>
                <p className="failure-head">
                  <code className="test-id">{failure.test_id}</code>
                  <span className="failure-owner">{failure.branch}&apos;s test</span>
                </p>
                {failure.excerpt && (
                  <CodeBlock code={failure.excerpt} label={`Failure in ${failure.test_id}`} marked={[1]} />
                )}
              </li>
            ))}
          </ul>
        )}
        {branchTests.runs.length > 0 && (
          <details className="more">
            <summary>
              All runs <span className="tabular">({branchTests.duration_s.toFixed(1)}s)</span>
            </summary>
            {confirmed && <p className="more-note">{branchTests.reason}</p>}
            <ul className="run-list">
              {branchTests.runs.map((run, index) => (
                <li key={`${run.target}-${run.runner ?? 'pytest'}-${run.package ?? ''}-${index}`}>
                  <strong>{TARGET_LABEL[run.target]}</strong> ({run.ref}
                  {run.runner && run.runner !== 'pytest' ? `, ${run.runner} in ${run.package || '.'}` : ''},{' '}
                  {run.files.length} file{run.files.length === 1 ? '' : 's'}): {runSummary(run)}
                </li>
              ))}
            </ul>
            {(branchTests.setup?.length ?? 0) > 0 && (
              <p className="more-note" data-testid="branch-test-setup">
                JS setup
                {(branchTests.install_s ?? 0) > 0 || (branchTests.build_s ?? 0) > 0
                  ? ` (install ${(branchTests.install_s ?? 0).toFixed(1)}s, builds ${(branchTests.build_s ?? 0).toFixed(1)}s)`
                  : ''}
                : {branchTests.setup?.join('; ')}
              </p>
            )}
            {branchTests.sandbox && <p className="more-note">Sandbox: {branchTests.sandbox}</p>}
          </details>
        )}
      </div>
    </section>
  );
};
