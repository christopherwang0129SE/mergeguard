import type { Verification, VerifyRun } from '../types';

interface VerificationDetailsProps {
  verification: Verification;
}

const BADGE: Record<Verification['status'], { label: string; className: string }> = {
  confirmed: { label: 'Confirmed by intent test (Nemotron)', className: 'badge badge-confirmed' },
  suspected: { label: 'Suspected: intent test inconclusive', className: 'badge badge-suspected' },
  error: { label: 'Intent test error', className: 'badge badge-suspected' },
  skipped: { label: 'Intent test skipped', className: 'badge badge-suspected' },
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
 * Nemotron intent-test (verify-by-test) result for one pair: a status badge plus an expandable
 * section with the generated pytest test and the per-checkout outcomes.
 */
export const VerificationDetails = ({ verification }: VerificationDetailsProps) => {
  const badge = BADGE[verification.status];
  return (
    <div className="verification" data-testid="verification">
      <span className={badge.className} title={verification.reason}>
        {badge.label}
      </span>
      <div className="verification-reason">{verification.reason}</div>
      {verification.test_code && (
        <details className="verification-details">
          <summary>
            Generated test ({verification.attempts} attempt
            {verification.attempts === 1 ? '' : 's'}
            {verification.cached_test ? ', cached' : ''}, {verification.duration_s.toFixed(1)}s)
          </summary>
          {verification.runs.length > 0 && (
            <ul className="verification-runs">
              {verification.runs.map((run) => (
                <li key={run.target}>
                  <strong>{TARGET_LABEL[run.target]}</strong> ({run.ref}): {runSummary(run)}
                </li>
              ))}
            </ul>
          )}
          {verification.confirming_tests && verification.confirming_tests.length > 0 && (
            <div className="verification-confirming">
              Fails on merge: {verification.confirming_tests.join(', ')}
            </div>
          )}
          <pre className="verification-code">
            <code>{verification.test_code}</code>
          </pre>
          {verification.sandbox && (
            <div className="verification-sandbox">Sandbox: {verification.sandbox}</div>
          )}
        </details>
      )}
    </div>
  );
};
