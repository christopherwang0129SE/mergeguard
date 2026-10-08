"""Pydantic models for the Sentinel API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    """Analyze conflict risk across branches of a repo.

    Provide either a local `repo_path` or a `repo_url` to clone.
    """

    repo_path: str | None = Field(None, description="Absolute path to a local git repo.")
    repo_url: str | None = Field(None, description="Git URL to clone (https).")
    base_branch: str | None = Field(None, description="Integration branch (default: main).")
    branches: list[str] | None = Field(
        None, description="Branches to compare. Defaults to all non-base local branches."
    )
    use_cache: bool = Field(True, description="Reuse cached Nemotron results for identical SHAs.")


class ChangedFile(BaseModel):
    path: str
    added_lines: int
    removed_lines: int
    # Changed line ranges on the *new* side, as (start, end) inclusive.
    hunks: list[tuple[int, int]]


class BranchDiff(BaseModel):
    branch: str
    base: str
    merge_base: str | None
    ahead: int
    files: list[ChangedFile]


class FileCollision(BaseModel):
    path: str
    # Whether changed regions actually overlap/are adjacent (true conflict risk),
    # vs. merely touching the same file.
    overlapping: bool
    branch_a_hunks: list[tuple[int, int]]
    branch_b_hunks: list[tuple[int, int]]


RiskLevel = Literal["none", "low", "medium", "high"]
ScoreSource = Literal["model", "git"]
# Why a pair kept the git score. Absent when `score_source` is `model`.
FallbackReason = Literal[
    "no_api_key", "disabled", "prefiltered", "pair_cap", "model_error", "unparsable"
]


class PairRisk(BaseModel):
    branch_a: str
    branch_b: str
    score: float = Field(ge=0, le=100)
    level: RiskLevel
    shared_files: int
    overlapping_files: int
    collisions: list[FileCollision]
    rationale: str
    # Additive. `score` / `level` / `rationale` stay the values to display.
    # `score_source` says whether those came from Nemotron or the git heuristic.
    score_source: ScoreSource = "git"
    git_score: float | None = Field(default=None, ge=0, le=100)
    git_rationale: str | None = None
    model_score: float | None = Field(default=None, ge=0, le=100)
    model_level: RiskLevel | None = None
    model_rationale: str | None = None
    fallback_reason: FallbackReason | None = None
    # Static prefilter signal (cheap, before any LLM call).
    symbol_overlap: list[str] = Field(default_factory=list)
    prefilter_candidate: bool = True
    # Model call accounting for this pair (0 tokens when served from cache).
    model_cached: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0


class UsageSummary(BaseModel):
    """LLM cost accounting for one /api/analyze request."""

    model: str
    llm_calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    input_usd_per_1m: float
    output_usd_per_1m: float
    pairs_total: int = 0
    pairs_prefiltered: int = 0
    pairs_sent_to_model: int = 0
    pairs_capped: int = 0
    top_k: int = 0
    elapsed_s: float = 0.0


class AnalyzeResponse(BaseModel):
    base_branch: str
    branches: list[str]
    diffs: list[BranchDiff]
    pairs: list[PairRisk]
    # Set when at least one pair's published score used the model.
    risk_model: str | None = None
    usage: UsageSummary | None = None
