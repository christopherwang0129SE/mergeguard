import type { RunState, StageProgress } from '../utils/run';
import { STAGES, formatClock, formatDuration } from '../utils/run';

export type PipelineMode = 'idle' | 'live' | 'blind' | 'finished';

interface PipelineProps {
  run: RunState;
  /**
   * idle: nothing run yet, the stages explain themselves.
   * live: stage events stream in. blind: the backend answers in one
   * response, so no stage is known until it ends. finished: the run is over.
   */
  mode: PipelineMode;
  elapsedS?: number;
  onCancel?: () => void;
}

const stateText = (stage: StageProgress, mode: PipelineMode): string => {
  switch (stage.state) {
    case 'running':
      return 'running';
    case 'done':
      return stage.startedAt !== undefined && stage.endedAt !== undefined
        ? formatDuration(stage.endedAt - stage.startedAt)
        : 'done';
    case 'skipped':
      return 'skipped';
    default:
      return mode === 'live' || mode === 'blind' ? 'waiting' : mode === 'finished' ? 'not run' : '';
  }
};

/**
 * The analysis pipeline as a row of stages that light up as the stream
 * reports them. Before a run it doubles as the explanation of what will
 * happen; after a run it keeps the time each stage took.
 */
export const Pipeline = ({ run, mode, elapsedS = 0, onCancel }: PipelineProps) => {
  const running = mode === 'live' || mode === 'blind';
  const content = (
    <section className="pipeline" data-mode={mode} aria-labelledby="pipeline-heading" data-testid="pipeline">
      <div className="pipeline-head">
        <h2 id="pipeline-heading" className="pipeline-title">
          {mode === 'idle' ? 'How a run works' : running ? 'Analyzing' : 'Pipeline'}
        </h2>
        {running && (
          <div className="pipeline-clock">
            <span className="sr-only">Elapsed</span>
            <span className="clock tabular" data-testid="elapsed" aria-hidden="false">
              {formatClock(elapsedS)}
            </span>
            {onCancel && (
              <button type="button" className="btn btn-quiet" onClick={onCancel}>
                Cancel
              </button>
            )}
          </div>
        )}
      </div>
      <ol className="stages">
        {STAGES.map((def) => {
          const stage = run.stages[def.id];
          const text = stateText(stage, mode);
          return (
            <li key={def.id} className="stage" data-state={stage.state} data-testid={`stage-${def.id}`}>
              <span className="stage-bar" aria-hidden="true"></span>
              <span className="stage-title">{def.title}</span>
              {text && <span className="stage-state tabular">{text}</span>}
              {mode === 'idle' ? (
                <span className="stage-about">{def.about}</span>
              ) : (
                stage.detail && <span className="stage-detail">{stage.detail}</span>
              )}
            </li>
          );
        })}
      </ol>
      {mode === 'blind' && (
        <p className="pipeline-note">
          This backend has no live progress stream, so stages show when the whole run finishes.
        </p>
      )}
    </section>
  );
  return mode === 'finished' ? (
    <details className="pipeline-history">
      <summary>Run details <span>Pipeline stages and timings</span></summary>
      {content}
    </details>
  ) : content;
};
