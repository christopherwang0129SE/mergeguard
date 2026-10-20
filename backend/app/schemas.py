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
    verify: bool = Field(
        True, description="Run verify-by-test on high-risk pairs (local/demo repos only)."
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
ScoreSource = Literal["model", "git"]
# Why a pair kept the git score. Absent when `score_source` is `model`.
FallbackReason = Literal[
    "no_api_key", "disabled", "prefiltered", "pair_cap", "model_error", "unparsable"
]
VerifyStatus = Literal["confirmed", "suspected", "error", "skipped"]


class VerifyRun(BaseModel):
    """One execution of the generated test on a checkout."""

    target: Literal["a", "b", "base", "merge"]
    ref: str
    selection: str
    exit_code: int | None
    passed: int = 0
    failed: int = 0
    errors: int = 0
    timed_out: bool = False
    duration_s: float = 0.0
    output: str = ""
    # Per test function: "passed" | "failed" | "error" | "skipped".
    cases: dict[str, str] = Field(default_factory=dict)
    # Per failed test function: line in the test module where it failed.
    failure_lines: dict[str, int] = Field(default_factory=dict)


class DocSource(BaseModel):
    """A Tavily hit attached to a pair. The snippet body stays in the prompt."""

    title: str = ""
    url: str
    query: str = ""


class Verification(BaseModel):
    """Verify-by-test result for one pair.

    ``confirmed``: branch A's intent tests pass on A, branch B's pass on B,
    and at least one intent test that captures its branch's change (it fails
    on the merge-base) fails on the merge of A+B.
    ``suspected``: ran, but did not meet that bar. ``error``: could not
    generate or run a usable test. ``skipped``: not eligible (see reason).
    """

    status: VerifyStatus
    reason: str = ""
    test_code: str | None = None
    test_path: str | None = None
    runs: list[VerifyRun] = Field(default_factory=list)
    attempts: int = 0
    cached_test: bool = False
    sandbox: str | None = None
    merge_conflict: bool = False
    # Intent tests that pass on their branch, fail on the base, fail on the merge.
    confirming_tests: list[str] = Field(default_factory=list)
    duration_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Set when this verify run consulted Tavily. Empty if it did not.
    docs_sources: list[DocSource] = Field(default_factory=list)


BranchTestStatus = Literal["confirmed", "passed", "skipped", "unsupported", "error"]


class BranchTestRun(BaseModel):
    """One run (pytest, or vitest/jest for one JS package) of the branches' own
    test files on a checkout."""

    target: Literal["a", "b", "base", "merge", "merge-rerun"]
    ref: str
    files: list[str] = Field(default_factory=list)
    exit_code: int | None
    passed: int = 0
    failed: int = 0
    errors: int = 0
    timed_out: bool = False
    duration_s: float = 0.0
    runner: str = "pytest"
    package: str = ""  # JS: package dir the runner ran in
    problem: str = ""  # why the run produced no results (setup failure, startup error)


class BranchTestFailure(BaseModel):
    """A branch's own test that passes on its branch and fails on the merge."""

    test_id: str
    branch: str  # whose test file it is ("both" if both branches changed it)
    file: str
    excerpt: str = ""


class BranchTests(BaseModel):
    """Deterministic check, no LLM: the test files each branch added or
    modified, run on their own branch and on the merge of A+B.

    ``confirmed``: at least one of those tests passes on its branch, fails on
    the merge (twice) and does not already fail on the merge-base.
    ``passed``: ran, no such test. ``skipped``: no test files changed, or a
    textual merge conflict. ``unsupported``: only test files we cannot run
    (JS/TS without a vitest/jest setup or a node binary). ``error``: could not
    run (see reason).
    """

    status: BranchTestStatus
    reason: str = ""
    files_a: list[str] = Field(default_factory=list)
    files_b: list[str] = Field(default_factory=list)
    unsupported_files: list[str] = Field(default_factory=list)
    runs: list[BranchTestRun] = Field(default_factory=list)
    confirming: list[BranchTestFailure] = Field(default_factory=list)
    merge_conflict: bool = False
    sandbox: str | None = None
    duration_s: float = 0.0
    setup: list[str] = Field(default_factory=list)  # JS: install / build notes
    install_s: float = 0.0  # JS dependency install time spent in this check (0 when cached)
    build_s: float = 0.0  # JS workspace build time spent in this check


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
    # One line per overlap: who changed the symbol, who changes/calls it, where.
    symbol_evidence: list[str] = Field(default_factory=list)
    prefilter_candidate: bool = True
    # Exact static signature checks of the evidence (parsed both sides):
    # call sites that no longer match a changed signature, and signature
    # changes every call site still matches.
    static_breaks: list[str] = Field(default_factory=list)
    static_compatible: list[str] = Field(default_factory=list)
    # Parts of the model rationale the static checks contradict.
    unsupported_claims: list[str] = Field(default_factory=list)
    # Model call accounting for this pair (0 tokens when served from cache).
    model_cached: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Verify-by-test result (only for high-risk pairs on local/demo repos).
    verification: Verification | None = None
    # Tavily hits for third-party libraries this pair imports. Empty when the
    # lookup did not run (no key, disabled, nothing third-party, or it failed).
    docs_sources: list[DocSource] = Field(default_factory=list)
    # Branch tests on the merge (deterministic, no LLM; local allowed repos only).
    branch_tests: BranchTests | None = None
    # Set when something other than the git/model blend decided `score`/`level`,
    # e.g. "raised: confirmed by test". `blended_score` keeps the blend.
    score_note: str | None = None
    blended_score: float | None = Field(default=None, ge=0, le=100)


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
    verify_pairs: int = 0
    verify_confirmed: int = 0
    branch_test_pairs: int = 0
    branch_test_confirmed: int = 0
    elapsed_s: float = 0.0


class AnalyzeResponse(BaseModel):
    base_branch: str
    branches: list[str]
    diffs: list[BranchDiff]
    pairs: list[PairRisk]
    # Set when at least one pair's published score used the model.
    risk_model: str | None = None
    usage: UsageSummary | None = None


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


FileStatus = Literal["modified", "added", "deleted", "unchanged"]
DiffLineKind = Literal["context", "add", "del"]


class PairDiffRequest(BaseModel):
    """Both branches' diffs against the base, for the side-by-side view.

    Repo resolution matches ``/api/analyze``: local path or git URL, including
    a GitHub pull request URL.
    """

    repo_path: str | None = Field(None, description="Absolute path to a local git repo.")
    repo_url: str | None = Field(None, description="Git URL to clone (https), or a GitHub PR URL.")
    base_branch: str | None = Field(None, description="Integration branch (default: main).")
    branch_a: str = Field(..., min_length=1)
    branch_b: str = Field(..., min_length=1)
    context: int = Field(3, ge=0, le=100, description="Unified-diff context lines.")


class DiffLine(BaseModel):
    kind: DiffLineKind
    old_no: int | None = None
    new_no: int | None = None
    text: str


class DiffHunk(BaseModel):
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[DiffLine]


class FileSide(BaseModel):
    status: FileStatus
    patch: str = ""
    hunks: list[DiffHunk] = Field(default_factory=list)
    truncated: bool = False


class PairDiffFile(BaseModel):
    path: str
    shared: bool
    overlapping: bool
    a: FileSide
    b: FileSide


class PairDiffResponse(BaseModel):
    base_branch: str
    branch_a: str
    branch_b: str
    merge_base_a: str
    merge_base_b: str
    files: list[PairDiffFile]
