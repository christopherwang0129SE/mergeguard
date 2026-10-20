import { forwardRef } from 'react';
import type { ErrorHelp } from '../utils/errors';

interface ErrorPanelProps {
  help: ErrorHelp;
  onRetry?: () => void;
}

/** A failed analysis: what happened, the raw cause, and the command that fixes it. */
export const ErrorPanel = forwardRef<HTMLHeadingElement, ErrorPanelProps>(({ help, onRetry }, ref) => (
  <section className="problem" aria-labelledby="error-heading">
    <h2 id="error-heading" ref={ref} tabIndex={-1}>
      {help.title}
    </h2>
    <p className="problem-cause">{help.body}</p>
    {help.fix && (
      <>
        <p>{help.fix.text}</p>
        {help.fix.command && (
          <pre className="command" role="region" tabIndex={0} aria-label="Command to run">
            <code>{help.fix.command}</code>
          </pre>
        )}
      </>
    )}
    {onRetry && (
      <button type="button" className="btn" onClick={onRetry}>
        Try again
      </button>
    )}
  </section>
));
ErrorPanel.displayName = 'ErrorPanel';
