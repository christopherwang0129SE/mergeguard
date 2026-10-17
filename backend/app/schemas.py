"""Pydantic models for the Sentinel API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class PairRisk(BaseModel):
    branch_a: str
    branch_b: str
    score: float = Field(ge=0, le=100)
    level: RiskLevel
    shared_files: int
    overlapping_files: int
    collisions: list[FileCollision]
    rationale: str


class AnalyzeResponse(BaseModel):
    base_branch: str
    branches: list[str]
    diffs: list[BranchDiff]
    pairs: list[PairRisk]


class ResolveRequest(BaseModel):
    """Propose a merge of committed source into target; never apply it in-place."""

    model_config = ConfigDict(extra="forbid")

    repo_path: str = Field(min_length=1)
    target_branch: str = Field(min_length=1)
    source_branch: str = Field(min_length=1)
    research_query: str | None = Field(None, max_length=500)
    run_tests: bool = False


class FileResolution(BaseModel):
    path: str
    status: Literal["resolved", "needs_review"]
    explanation: str


class ValidationResult(BaseModel):
    name: str
    status: Literal["passed", "failed", "skipped"]
    output: str = ""


class ResolveResponse(BaseModel):
    target_branch: str
    source_branch: str
    target_commit: str
    source_commit: str
    status: Literal["clean", "proposed", "needs_review", "validation_failed"]
    files: list[FileResolution]
    patch: str | None = None
    validations: list[ValidationResult]
    research_sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GitHubPullRequest(BaseModel):
    number: int
    title: str
    author: str
    url: str
    head_branch: str
    base_branch: str
    updated_at: str


class GitHubPullRequestSyncResponse(BaseModel):
    repository: str
    pull_requests: list[GitHubPullRequest]
    analysis: AnalyzeResponse
