import type { AnalyzeResponse, PairRisk, StageId, StageStatus, StreamEvent } from '../types';

export interface StageDef {
  id: StageId;
  title: string;
  /** What the stage does, for the empty state and screen readers. */
  about: string;
}

/** The pipeline in the order the backend runs it. */
export const STAGES: StageDef[] = [
  { id: 'resolve', title: 'Open repo', about: 'Opens the local repository or clones the URL.' },
  { id: 'diff', title: 'Diff', about: 'Diffs every branch against the base branch.' },
  { id: 'prefilter', title: 'Prefilter', about: 'Keeps pairs that share files or functions. Plain git, no LLM.' },
  { id: 'model', title: 'Nemotron', about: 'Nemotron reads both diffs and scores how the changes interact.' },
  { id: 'verify', title: 'Verify by test', about: 'Nemotron writes a test of both intents and runs it on A, B, base and the merge.' },
  { id: 'branch_tests', title: 'Branch tests', about: "Each branch's own tests run on the merge. No LLM." },
];

export type StageState = 'idle' | 'running' | 'done' | 'skipped';

export interface StageProgress {
  state: StageState;
  label?: string;
  detail?: string | null;
  startedAt?: number;
  endedAt?: number;
}

export interface RunState {
  stages: Record<StageId, StageProgress>;
  branches: string[];
  baseBranch: string | null;
  /** Pairs scored so far, in arrival order, keyed by `pairKey`. */
  pairs: PairRisk[];
  /** Seconds since start of the latest event the server sent. */
  lastT: number;
}

export const pairKey = (pair: Pick<PairRisk, 'branch_a' | 'branch_b'>): string => `${pair.branch_a}..${pair.branch_b}`;

export const initialRun = (): RunState => ({
  stages: Object.fromEntries(STAGES.map((s) => [s.id, { state: 'idle' }])) as Record<StageId, StageProgress>,
  branches: [],
  baseBranch: null,
  pairs: [],
  lastT: 0,
});

const STATE_FOR: Record<StageStatus, StageState> = { start: 'running', done: 'done', skipped: 'skipped' };

/** Fold one stream event into the run. Pure, so the reducer is testable. */
export const reduceRun = (run: RunState, event: StreamEvent): RunState => {
  const lastT = typeof event.t === 'number' ? Math.max(run.lastT, event.t) : run.lastT;
  switch (event.type) {
    case 'stage': {
      if (!(event.stage in run.stages)) return { ...run, lastT };
      const prev = run.stages[event.stage];
      const state = STATE_FOR[event.status] ?? prev.state;
      const next: StageProgress = {
        ...prev,
        state,
        label: event.label || prev.label,
        detail: event.detail ?? prev.detail,
        startedAt: event.status === 'start' ? event.t : prev.startedAt ?? event.t,
        endedAt: event.status === 'start' ? undefined : event.t,
      };
      return { ...run, lastT, stages: { ...run.stages, [event.stage]: next } };
    }
    case 'branches':
      return { ...run, lastT, branches: event.branches, baseBranch: event.base_branch };
    case 'pair': {
      const key = pairKey(event.pair);
      const index = run.pairs.findIndex((p) => pairKey(p) === key);
      const pairs = index >= 0 ? run.pairs.map((p, i) => (i === index ? event.pair : p)) : [...run.pairs, event.pair];
      return { ...run, lastT, pairs };
    }
    case 'result':
      return finishRun({ ...run, lastT }, event.result);
    default:
      return { ...run, lastT };
  }
};

/** The final result is authoritative: its pairs replace the provisional ones. */
export const finishRun = (run: RunState, result: AnalyzeResponse): RunState => ({
  ...run,
  branches: result.branches,
  baseBranch: result.base_branch,
  pairs: result.pairs,
  stages: Object.fromEntries(
    Object.entries(run.stages).map(([id, stage]) => [
      id,
      stage.state === 'running' ? { ...stage, state: 'done' as const, endedAt: run.lastT } : stage,
    ])
  ) as Record<StageId, StageProgress>,
});

/** The stage that is running now, if any. */
export const activeStage = (run: RunState): StageDef | undefined =>
  [...STAGES].reverse().find((s) => run.stages[s.id].state === 'running');

/** "0:07" / "1:42" / "12:03". */
export const formatClock = (seconds: number): string => {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

/** "0.4s" / "12s" / "2m 05s" for a finished stage. */
export const formatDuration = (seconds: number): string => {
  if (seconds < 10) return `${Math.max(0, seconds).toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
};
