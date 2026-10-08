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
export type FallbackReason = 'no_api_key' | 'disabled' | 'pair_cap' | 'model_error' | 'unparsable' | null;

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
}

export interface AnalyzeResponse {
  base_branch: string;
  branches: string[];
  diffs: BranchDiff[];
  pairs: PairRisk[];
  risk_model?: string | null;
}

export interface HealthResponse {
  status: string;
  nebius_ready: boolean;
  nebius_enabled: boolean;
  nebius_model: string | null;
  tavily_ready: boolean;
}