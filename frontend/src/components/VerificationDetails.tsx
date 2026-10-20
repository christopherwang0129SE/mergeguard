import type { Verification, VerifyRun } from '../types';
import { failureExcerpts } from '../utils/evidence';
import { CodeBlock } from './CodeBlock';
import { TruthTable } from './TruthTable';
import { keptRuns } from '../utils/verification';

interface VerificationDetailsProps {
  verification: Verification;
  branchA: string;
  branchB: string;
}

const STATUS: Record<Verification['status'], { label: string; tone: 'fail' | 'neutral' | 'warn' }> = {
  confirmed: { label: 'Confirmed by intent test', tone: 'fail' },
  suspected: { label: 'Suspected: intent test inconclusive', tone: 'warn' },
  error: { label: 'Intent test error', tone: 'warn' },
  skipped: { label: 'Intent test skipped', tone: 'neutral' },
};

const TARGET_LABEL: Record<VerifyRun['target'], string> = {
  a: 'Branch A',
  b: 'Branch B',
  base: 'Merge-base',
  merge: 'Merge A+B',
};

const runSummary = (run: VerifyRun): string => {
  if (run.timed_out) {
    return 'timed out';
  }
  const parts = [`${run.passed} passed`];
  if (run.failed > 0) parts.push(`${run.failed} failed`);
  if (run.errors > 0) parts.push(`${run.errors} errors`);
  return parts.join(', ');
};

/**
 * Nemotron intent-test (verify-by-test) result for one pair: the status, the
 * pass/pass/fail proof, the assertion that failed on the merge, and the
 * generated test with the failing lines marked.
 */
export const VerificationDetails = ({ verification, branchA, branchB }: VerificationDetailsProps) => {
  const status = STATUS[verification.status];
  const confirming = verification.confirming_tests ?? [];
  const merge = verification.runs.find((run) => run.target === 'merge');
  const excerpts = failureExcerpts(merge?.output).filter(
    (excerpt) => confirming.length === 0 || confirming.includes(excerpt.test)
  );
  const failingLines = confirming
    .map((name) => merge?.failure_lines?.[name])
    .filter((line): line is number => typeof line === 'number');
  const fileName = verification.test_path?.split('/').pop();

  return (
    <section className="block" data-status={verification.status} data-testid="verification" aria-labelledby="verify-heading">
      <h3 id="verify-heading" className="block-title">
        Verify by test <span className="block-sub">Nemotron writes a test of each branch&apos;s intent</span>
      </h3>
      <div className="block-body">
        <p className="status-line">
          <span className="status" data-tone={status.tone}>
            {status.label}
          </span>
          {verification.status !== 'confirmed' && <span className="status-reason">{verification.reason}</span>}
        </p>
        {verification.runs.length > 0 && (
          <TruthTable runs={keptRuns(verification)} branchA={branchA} branchB={branchB} label="Intent test results per checkout" />
        )}
        {confirming.length > 0 && (
          <p className="fails-on-merge" data-many={confirming.length > 1 || undefined}>
            Fails on merge:{' '}
            {confirming.map((name) => (
              <code key={name} className="test-id">
                {name}
              </code>
            ))}
          </p>
        )}
        {excerpts.map((excerpt) => (
          <CodeBlock
            key={excerpt.test}
            code={excerpt.lines.join('\n')}
            label={`Failure in ${excerpt.test} on the merge`}
            markFailures
          />
        ))}
        {verification.test_code && (
          <details className="more" open={verification.status === 'confirmed' || undefined}>
            <summary>
              Generated test
              {fileName && (
                <>
                  {' '}
                  <code>{fileName}</code>
                </>
              )}{' '}
              <span className="summary-meta tabular">
                {`(${
                  verification.cached_test
                    ? 'cached'
                    : `${verification.attempts} attempt${verification.attempts === 1 ? '' : 's'}`
                }, ${verification.duration_s.toFixed(1)}s)`}
              </span>
            </summary>
            <CodeBlock code={verification.test_code} label="Generated test code" numbered marked={failingLines} />
          </details>
        )}
        {verification.runs.length > 0 && (
          <details className="more">
            <summary>All runs</summary>
            {verification.status === 'confirmed' && <p className="more-note">{verification.reason}</p>}
            <ul className="run-list">
              {verification.runs.map((run) => (
                <li key={run.target}>
                  <strong>{TARGET_LABEL[run.target]}</strong> ({run.ref}): {runSummary(run)}
                </li>
              ))}
            </ul>
            {merge?.output && <CodeBlock code={merge.output} label="Test output on the merge" markFailures />}
            {verification.sandbox && <p className="more-note">Sandbox: {verification.sandbox}</p>}
          </details>
        )}
      </div>
    </section>
  );
};
