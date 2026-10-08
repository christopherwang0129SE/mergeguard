export interface AnalyzeRequest {
  repo_path?: string;
  repo_url?: string;
  base_branch?: string;
  branches?: string[];
}

export interface ChangedFile {
  path: string;
  added_lines: number;
  removed_lines: number;
  hunks: Array<[number, number]>;
}

export interface BranchDiff {
  branch: string;
  base: string;
  merge_base?: string | null;
  ahead: number;
  files: ChangedFile[];
}

export interface FileCollision {
  path: string;
  overlapping: boolean;
  branch_a_hunks: Array<[number, number]>;
  branch_b_hunks: Array<[number, number]>;
}

export type RiskLevel = 'none' | 'low' | 'medium' | 'high';
export type ScoreSource = 'model' | 'git';
export type FallbackReason =
  | 'no_api_key'
  | 'disabled'
  | 'prefiltered'
  | 'pair_cap'
  | 'model_error'
  | 'unparsable'
  | null;

export interface PairRisk {
  branch_a: string;
  branch_b: string;
  score: number; // 0..100
  level: RiskLevel;
  shared_files: number;
  overlapping_files: number;
  collisions: FileCollision[];
  rationale: string;
  score_source: ScoreSource;
  git_score?: number | null;
  git_rationale?: string | null;
  model_score?: number | null;
  model_level?: RiskLevel | null;
  model_rationale?: string | null;
  fallback_reason?: FallbackReason;
  symbol_overlap?: string[];
  symbol_evidence?: string[];
  prefilter_candidate?: boolean;
  static_breaks?: string[];
  static_compatible?: string[];
  unsupported_claims?: string[];
  model_cached?: boolean;
  prompt_tokens?: number;
  completion_tokens?: number;
  verification?: Verification | null;
  branch_tests?: BranchTests | null;
  score_note?: string | null;
  blended_score?: number | null;
}

export type VerifyStatus = 'confirmed' | 'suspected' | 'error' | 'skipped';

export interface VerifyRun {
  target: 'a' | 'b' | 'base' | 'merge';
  ref: string;
  selection: string;
  exit_code: number | null;
  passed: number;
  failed: number;
  errors: number;
  timed_out: boolean;
  duration_s: number;
  output: string;
  cases?: Record<string, string>;
  failure_lines?: Record<string, number>;
}

export interface Verification {
  status: VerifyStatus;
  reason: string;
  test_code?: string | null;
  test_path?: string | null;
  runs: VerifyRun[];
  attempts: number;
  cached_test: boolean;
  sandbox?: string | null;
  merge_conflict: boolean;
  confirming_tests?: string[];
  duration_s: number;
  prompt_tokens: number;
  completion_tokens: number;
}

export type BranchTestStatus = 'confirmed' | 'passed' | 'skipped' | 'unsupported' | 'error';

export interface BranchTestRun {
  target: 'a' | 'b' | 'base' | 'merge' | 'merge-rerun';
  ref: string;
  files: string[];
  exit_code: number | null;
  passed: number;
  failed: number;
  errors: number;
  timed_out: boolean;
  duration_s: number;
  /** "pytest", or "vitest" / "jest" for a JS/TS package. */
  runner?: string;
  /** JS: package directory the runner ran in. */
  package?: string;
  /** Why the run produced no results (setup failure, startup error). */
  problem?: string;
}

export interface BranchTestFailure {
  test_id: string;
  branch: string;
  file: string;
  excerpt: string;
}

/** Deterministic check (no LLM): each branch's own changed tests run on A+B. */
export interface BranchTests {
  status: BranchTestStatus;
  reason: string;
  files_a: string[];
  files_b: string[];
  unsupported_files: string[];
  runs: BranchTestRun[];
  confirming: BranchTestFailure[];
  merge_conflict: boolean;
  sandbox?: string | null;
  duration_s: number;
  /** JS: dependency install / workspace build notes. */
  setup?: string[];
  /** JS dependency install time spent in this check (0 when reused from cache). */
  install_s?: number;
  /** JS workspace build time spent in this check. */
  build_s?: number;
}

export interface UsageSummary {
  model: string;
  llm_calls: number;
  cache_hits: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  input_usd_per_1m: number;
  output_usd_per_1m: number;
  pairs_total: number;
  pairs_prefiltered: number;
  pairs_sent_to_model: number;
  pairs_capped: number;
  top_k: number;
  verify_pairs?: number;
  verify_confirmed?: number;
  branch_test_pairs?: number;
  branch_test_confirmed?: number;
  elapsed_s: number;
}

export interface AnalyzeResponse {
  base_branch: string;
  branches: string[];
  diffs: BranchDiff[];
  pairs: PairRisk[];
  risk_model?: string | null;
  usage?: UsageSummary | null;
}

export interface HealthResponse {
  status: string;
  nebius_ready: boolean;
  nebius_enabled: boolean;
  nebius_model: string | null;
  model?: string | null;
  provider?: string | null;
  base_url?: string | null;
  tavily_ready: boolean;
}