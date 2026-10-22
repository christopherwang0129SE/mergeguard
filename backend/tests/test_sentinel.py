"""End-to-end test: build a real git repo with conflicting branches."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.schemas import BranchDiff, BranchTests, ChangedFile, FileCollision, PairRisk, Verification
from app.services import sentinel, verifier
from app.services.nemotron import build_pair_prompt


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _adjacent_repo(tmp_path: Path) -> Path:
    """Two edits two lines apart: the hunk heuristic overlaps, git merges cleanly."""
    repo = tmp_path / "near"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    base = [f"line {i}" for i in range(1, 21)]
    (repo / "app.py").write_text("\n".join(base) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    def branch(name: str, index: int, text: str) -> None:
        _git(repo, "checkout", "-q", "main")
        _git(repo, "checkout", "-q", "-b", name)
        lines = list(base)
        lines[index] = text
        (repo / "app.py").write_text("\n".join(lines) + "\n")
        _git(repo, "commit", "-qam", name)

    branch("near-a", 4, "line 5 CHANGED BY A")
    branch("near-b", 6, "line 7 CHANGED BY B")
    _git(repo, "checkout", "-q", "main")
    return repo


def test_overlap_is_high_risk(repo: Path) -> None:
    res = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"])
    pair = {frozenset((p.branch_a, p.branch_b)): p for p in res.pairs}

    ab = pair[frozenset(("feat-a", "feat-b"))]
    assert ab.overlapping_files == 1
    assert ab.level in ("medium", "high")
    assert ab.score >= 40

    # a and c share app.py but edit far-apart lines -> no overlap.
    ac = pair[frozenset(("feat-a", "feat-c"))]
    assert ac.shared_files == 1
    assert ac.overlapping_files == 0
    assert ac.score < ab.score


def test_highest_risk_pair_first(repo: Path) -> None:
    res = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"])
    assert {res.pairs[0].branch_a, res.pairs[0].branch_b} == {"feat-a", "feat-b"}


def test_finding_separates_textual_conflicts(repo: Path) -> None:
    res = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"])
    pair = {frozenset((p.branch_a, p.branch_b)): p for p in res.pairs}

    ab = pair[frozenset(("feat-a", "feat-b"))]
    assert ab.textual_conflict is True
    assert ab.level in ("medium", "high")
    assert ab.finding == "textual_conflict"

    ac = pair[frozenset(("feat-a", "feat-c"))]
    assert ac.textual_conflict is False
    assert ac.overlapping_files == 0
    assert ac.level == "low"
    assert ac.finding == "none"

    bc = pair[frozenset(("feat-b", "feat-c"))]
    assert bc.textual_conflict is False
    assert bc.finding == "none"


def test_pair_finding_confirmed_is_semantic_unless_git_conflicts() -> None:
    low = PairRisk(
        branch_a="a", branch_b="b", score=10, level="low",
        shared_files=0, overlapping_files=0, collisions=[], rationale="r",
        textual_conflict=False,
    )
    assert sentinel._pair_finding(low) == "none"
    verified = low.model_copy(update={"verification": Verification(status="confirmed")})
    assert sentinel._pair_finding(verified) == "semantic"
    branched = low.model_copy(update={"branch_tests": BranchTests(status="confirmed")})
    assert sentinel._pair_finding(branched) == "semantic"
    conflict = verified.model_copy(update={"textual_conflict": True, "level": "high"})
    assert sentinel._pair_finding(conflict) == "textual_conflict"
    unknown = low.model_copy(update={"textual_conflict": None, "level": "medium"})
    assert sentinel._pair_finding(unknown) == "semantic"


def _low_model(prompt: str) -> str:
    return json.dumps({"risk_score": 10, "risk_level": "low", "rationale": "No joint break."})


def test_clean_overlap_skips_the_hunk_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.nemotron.complete", _low_model)
    pair = sentinel.analyze(_adjacent_repo(tmp_path), "main", ["near-a", "near-b"]).pairs[0]
    assert pair.textual_conflict is False
    assert pair.overlapping_files == 1
    assert pair.git_score is not None and pair.git_score > 10
    plain = round((0.5 * pair.git_score) + (0.5 * 10), 1)
    assert pair.score == plain
    assert pair.score < pair.git_score
    assert pair.level == "low"
    assert pair.finding == "none"


def test_unknown_merge_keeps_the_hunk_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.git_service.textual_conflict", lambda *_a, **_k: None)
    monkeypatch.setattr("app.services.nemotron.complete", _low_model)
    pair = sentinel.analyze(_adjacent_repo(tmp_path), "main", ["near-a", "near-b"]).pairs[0]
    assert pair.textual_conflict is None
    assert pair.overlapping_files == 1
    assert pair.git_score is not None
    assert pair.score == pair.git_score
    assert pair.finding == "semantic"


def test_real_conflict_keeps_the_floor(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.nemotron.complete", _low_model)
    pair = sentinel.analyze(repo, "main", ["feat-a", "feat-b"]).pairs[0]
    assert pair.textual_conflict is True
    assert pair.overlapping_files == 1
    assert pair.git_score is not None and pair.score == pair.git_score
    assert pair.score > 10
    assert pair.finding == "textual_conflict"


def _line_after(text: str, prefix: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            return lines[index + 1]
    raise AssertionError(f"no line starts with {prefix!r}")


def _prompt_for(textual_conflict: bool | None, rationale: str, **extra: object) -> str:
    diff_a = BranchDiff(branch="a", base="main", merge_base="x", ahead=1, files=[])
    diff_b = BranchDiff(branch="b", base="main", merge_base="x", ahead=1, files=[])
    pair = PairRisk(
        branch_a="a", branch_b="b", score=47.5, level="medium", rationale=rationale,
        shared_files=1, overlapping_files=1, collisions=[], git_score=47.5,
        git_rationale=rationale, textual_conflict=textual_conflict, **extra,
    )
    return build_pair_prompt(
        base_branch="main", pair=pair, diff_a=diff_a, diff_b=diff_b,
        commits_a=["a edits top"], commits_b=["b edits top"], excerpt="diff",
    )


def test_prompt_states_git_merge_tree_result() -> None:
    likely = (
        "1 file(s) changed in overlapping regions by both branches — "
        "a textual merge conflict is likely."
    )
    clean = _prompt_for(False, likely, finding="none")
    unknown = _prompt_for(None, likely)
    conflict = _prompt_for(True, likely, finding="textual_conflict")

    assert _line_after(clean, "Git heuristic") == "Git merge (git merge-tree): merges cleanly"
    assert _line_after(unknown, "Git heuristic") == "Git merge (git merge-tree): unknown"
    assert _line_after(conflict, "Git heuristic") == (
        "Git merge (git merge-tree): reports a TEXTUAL CONFLICT "
        "(reported separately; score only semantic effects)"
    )
    # The merge line sits between the heuristic and the rationale.
    assert _line_after(clean, "Git merge (git merge-tree):").startswith("Git rationale:")
    assert _line_after(unknown, "Git merge (git merge-tree):") == f"Git rationale: {likely}"
    assert _line_after(conflict, "Git merge (git merge-tree):") == f"Git rationale: {likely}"


def test_clean_merge_replaces_conflict_likely_rationale() -> None:
    likely = (
        "1 file(s) changed in overlapping regions by both branches — "
        "a textual merge conflict is likely."
    )
    calm = (
        "1 shared file(s) but no overlapping regions — "
        "merges cleanly today, but coordinate before divergence grows."
    )
    clean = _prompt_for(False, likely)
    assert _line_after(clean, "Git merge (git merge-tree):") == (
        "Git rationale: Hunks are close together, but git merges them cleanly."
    )
    assert "conflict is likely" not in clean

    kept = _prompt_for(False, calm)
    assert "Git merge (git merge-tree): merges cleanly" in kept
    assert f"Git rationale: {calm}" in kept
    assert "Hunks are close together" not in kept


def _risk(**kwargs: object) -> PairRisk:
    data: dict = dict(
        branch_a="a", branch_b="b", score=10, level="low",
        shared_files=0, overlapping_files=0, collisions=[], rationale="r",
    )
    data.update(kwargs)
    return PairRisk(**data)


def _collision(path: str, overlapping: bool) -> FileCollision:
    return FileCollision(
        path=path, overlapping=overlapping,
        branch_a_hunks=[(1, 2)], branch_b_hunks=[(1, 2)],
    )


def test_excerpt_paths_shared_source_beats_changelog() -> None:
    pair = _risk(
        shared_files=3, overlapping_files=1,
        collisions=[
            _collision("lib.py", False),
            _collision("app.py", True),
            _collision("CHANGES.md", False),
        ],
    )
    files_a = [
        ChangedFile(path="lib.py", added_lines=1, removed_lines=0, hunks=[(10, 12)]),
        "CHANGES.md",
        ChangedFile(path="app.py", added_lines=1, removed_lines=0, hunks=[(1, 2)]),
        "README.rst",
    ]
    files_b = ["lib.py", "CHANGES.md", "app.py", "docs/guide.txt"]
    assert sentinel._excerpt_paths(pair, files_a, files_b) == ["app.py", "lib.py"]


def test_excerpt_paths_includes_config_only_one_branch_changed() -> None:
    pair = _risk(shared_files=1, collisions=[_collision("CHANGES.md", False)])
    got = sentinel._excerpt_paths(pair, ["CHANGES.md"], ["CHANGES.md", "pyproject.toml"])
    assert got == ["pyproject.toml"]


def test_excerpt_paths_docs_last_only_when_nothing_else() -> None:
    pair = _risk(
        shared_files=3,
        collisions=[
            _collision("CHANGES.md", True),
            _collision("docs/guide.py", False),
            _collision("tests/data/fixture.json", False),
        ],
    )
    files = ["tests/data/fixture.json", "CHANGES.md", "docs/guide.py"]
    assert sentinel._excerpt_paths(pair, files, files) == [
        "CHANGES.md", "docs/guide.py", "tests/data/fixture.json",
    ]

    # A real source file, a focus path, or a config file drops the docs.
    with_source = sentinel._excerpt_paths(
        pair, [*files, "src/app.py"], [*files, "src/app.py"],
    )
    assert with_source == ["src/app.py"]


def test_excerpt_paths_focus_before_config_and_caps_at_three() -> None:
    pair = _risk(
        shared_files=1, overlapping_files=1,
        collisions=[_collision("src/app.py", True)],
        symbol_evidence=["parse: changed by a (src/app.py), called by b (src/caller.py)"],
    )
    got = sentinel._excerpt_paths(
        pair,
        ["src/app.py", "CHANGES.md"],
        ["src/app.py", "CHANGES.md", "pyproject.toml", "setup.cfg", "tox.ini"],
    )
    assert got == ["src/app.py", "src/caller.py", "pyproject.toml"]


def test_excerpt_paths_non_code_and_test_data_rank_with_docs() -> None:
    pair = _risk(
        shared_files=4,
        collisions=[
            _collision("src/app.py", False),
            _collision("tests/data/sample.py", True),
            _collision("locales/en.json", False),
            _collision("README.md", False),
        ],
    )
    files = ["README.md", "locales/en.json", "tests/data/sample.py", "src/app.py"]
    assert sentinel._excerpt_paths(pair, files, files) == ["src/app.py"]

    pair = _risk(
        shared_files=3,
        collisions=[
            _collision("README.md", False),
            _collision("locales/en.json", False),
            _collision("tests/data/sample.py", True),
        ],
    )
    files = ["locales/en.json", "README.md", "tests/data/sample.py"]
    assert sentinel._excerpt_paths(pair, files, files) == [
        "README.md", "tests/data/sample.py", "locales/en.json",
    ]


@pytest.mark.parametrize("path", [
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    ".flake8",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".pre-commit-config.yaml",
    "frontend/package.json",
    "tsconfig.json",
    "web/tsconfig.app.json",
    ".eslintrc",
    "pkg/.eslintrc.cjs",
    "biome.json",
])
def test_excerpt_paths_counts_tooling_files_as_config(path: str) -> None:
    pair = _risk(shared_files=1, collisions=[_collision("CHANGES.md", False)])
    assert sentinel._excerpt_paths(pair, ["CHANGES.md"], ["CHANGES.md", path]) == [path]


def _clean_overlap_repo(tmp_path: Path) -> Path:
    """Two files, edits two lines apart.

    The hunk heuristic overlaps both files (git score >= 70) and git merges cleanly.
    """
    repo = tmp_path / "clean-high"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    base = [f"line {i}" for i in range(1, 21)]
    for name in ("app.py", "lib.py"):
        (repo / name).write_text("\n".join(base) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    def branch(name: str, index: int, text: str) -> None:
        _git(repo, "checkout", "-q", "main")
        _git(repo, "checkout", "-q", "-b", name)
        for filename in ("app.py", "lib.py"):
            lines = list(base)
            lines[index] = text
            (repo / filename).write_text("\n".join(lines) + "\n")
        _git(repo, "commit", "-qam", name)

    branch("near-a", 4, "line 5 CHANGED BY A")
    branch("near-b", 6, "line 7 CHANGED BY B")
    _git(repo, "checkout", "-q", "main")
    return repo


def test_clean_merge_high_needs_a_high_model_assessment() -> None:
    note = sentinel.CLEAN_MERGE_NOTE

    stayed = sentinel._cap_clean_merge(_risk(
        score=88, level="high", textual_conflict=False, score_source="model",
        model_level="high", model_score=90, git_score=80,
    ))
    assert (stayed.score, stayed.level, stayed.score_note, stayed.blended_score) == (88, "high", None, None)

    lifted = sentinel._cap_clean_merge(_risk(
        score=80, level="high", textual_conflict=False, score_source="model",
        model_level="medium", model_score=50, git_score=100,
    ))
    assert lifted.score == 69.0 and lifted.level == "medium"
    assert lifted.score_note == note and lifted.blended_score == 80
    assert lifted.model_level == "medium" and lifted.model_score == 50

    low_model = sentinel._cap_clean_merge(_risk(
        score=85, level="high", textual_conflict=False, score_source="model",
        model_level="low", model_score=20, git_score=100,
    ))
    assert low_model.score == 69.0 and low_model.level == "medium" and low_model.score_note == note

    fallback = sentinel._cap_clean_merge(_risk(
        score=80, level="high", textual_conflict=False, score_source="git",
        fallback_reason="no_api_key", git_score=80,
    ))
    assert fallback.score == 69.0 and fallback.level == "medium"
    assert fallback.score_note == note and fallback.blended_score == 80
    assert fallback.fallback_reason == "no_api_key" and fallback.git_score == 80

    for conflict in (True, None):
        kept = sentinel._cap_clean_merge(_risk(
            score=90, level="high", textual_conflict=conflict, score_source="git",
            fallback_reason="no_api_key", git_score=90,
        ))
        assert kept.score == 90 and kept.level == "high"
        assert kept.score_note is None and kept.blended_score is None

    unchanged = sentinel._cap_clean_merge(_risk(
        score=69, level="medium", textual_conflict=False, score_source="git", git_score=69,
    ))
    assert unchanged.score == 69 and unchanged.level == "medium" and unchanged.score_note is None

    confirmed = sentinel._cap_clean_merge(_risk(
        score=90, level="high", textual_conflict=False, score_source="model",
        model_level="medium", model_score=40, git_score=100,
        blended_score=80, score_note=sentinel.CONFIRMED_NOTE,
        verification=Verification(status="confirmed"),
    ))
    assert confirmed.score == 90 and confirmed.level == "high"
    assert confirmed.score_note == sentinel.CONFIRMED_NOTE and confirmed.blended_score == 80

    capped = sentinel._cap_clean_merge(_risk(
        score=80, level="high", textual_conflict=False, score_source="git",
        fallback_reason="no_api_key", git_score=80,
    ))
    raised = sentinel.apply_branch_tests(capped, BranchTests(status="confirmed"))
    assert raised.level == "high" and raised.score == 90.0
    assert raised.score_note == sentinel.BRANCH_TEST_NOTE and raised.blended_score == 80

    above = sentinel._cap_clean_merge(_risk(
        score=95, level="high", textual_conflict=False, score_source="git",
        fallback_reason="no_api_key", git_score=95,
    ))
    kept_high = sentinel.apply_branch_tests(above, BranchTests(status="confirmed"))
    assert kept_high.level == "high" and kept_high.score == 95.0
    assert kept_high.score_note == sentinel.BRANCH_TEST_NOTE and kept_high.blended_score == 95


def test_clean_merge_git_fallback_is_capped(tmp_path: Path) -> None:
    pair = sentinel.analyze(_clean_overlap_repo(tmp_path), "main", ["near-a", "near-b"]).pairs[0]
    assert pair.textual_conflict is False
    assert pair.score_source == "git" and pair.fallback_reason == "no_api_key"
    assert pair.overlapping_files == 2
    assert pair.git_score is not None and pair.git_score > 69
    assert pair.score == 69.0 and pair.level == "medium"
    assert pair.score_note == sentinel.CLEAN_MERGE_NOTE
    assert pair.blended_score == pair.git_score
    assert pair.finding == "semantic"


def test_textual_conflict_true_or_unknown_is_not_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _clean_overlap_repo(tmp_path)

    def analyze(value: bool | None):
        monkeypatch.setattr("app.services.git_service.textual_conflict", lambda *_a, **_k: value)
        return sentinel.analyze(repo, "main", ["near-a", "near-b"]).pairs[0]

    conflict = analyze(True)
    assert conflict.textual_conflict is True
    assert conflict.git_score is not None and conflict.score == conflict.git_score
    assert conflict.score > 69 and conflict.level == "high" and conflict.score_note is None
    assert conflict.finding == "textual_conflict"

    unknown = analyze(None)
    assert unknown.textual_conflict is None
    assert unknown.git_score is not None and unknown.score == unknown.git_score
    assert unknown.score > 69 and unknown.level == "high" and unknown.score_note is None


def _assessment(score: float, level: str, rationale: str):
    def complete(_prompt: str) -> str:
        return json.dumps({"risk_score": score, "risk_level": level, "rationale": rationale})

    return complete


def test_clean_merge_model_high_stays_high(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.nemotron.complete", _assessment(90, "high", "Breaks the caller."))
    pair = sentinel.analyze(_clean_overlap_repo(tmp_path), "main", ["near-a", "near-b"]).pairs[0]
    assert pair.textual_conflict is False
    assert pair.score_source == "model" and pair.model_level == "high"
    assert pair.score >= 70 and pair.level == "high"
    assert pair.score_note is None and pair.blended_score is None


def test_clean_merge_model_medium_is_capped_after_verify_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The published score is capped, but verify still selected the uncapped blend.

    ``is_high_risk`` reads level and score. After the cap both sit under 70, so
    selecting after the cap would skip this pair.
    """
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.nemotron.complete", _assessment(69, "medium", "Coordinate."))
    monkeypatch.setattr(settings, "branch_tests_enabled", False)
    pair = sentinel.analyze(
        _clean_overlap_repo(tmp_path), "main", ["near-a", "near-b"],
        verify=True, verify_allowed=False,
    ).pairs[0]
    assert pair.textual_conflict is False
    assert pair.score_source == "model" and pair.model_level == "medium" and pair.model_score == 69
    assert pair.verification is not None and pair.verification.status == "skipped"
    assert pair.blended_score is not None and pair.blended_score >= 70
    assert pair.score == 69.0 and pair.level == "medium"
    assert pair.score_note == sentinel.CLEAN_MERGE_NOTE
    uncapped = pair.model_copy(update={
        "score": pair.blended_score, "level": "high", "score_note": None, "blended_score": None,
    })
    assert verifier.is_high_risk(uncapped) is True
    assert verifier.is_high_risk(pair) is False


def test_clean_merge_verification_confirm_is_not_recapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nebius_api_key", "test-key")
    monkeypatch.setattr("app.services.nemotron.complete", _assessment(69, "medium", "Coordinate."))
    monkeypatch.setattr(settings, "branch_tests_enabled", False)
    monkeypatch.setattr(settings, "verify_allowed_roots", str(tmp_path))
    monkeypatch.setattr(
        "app.services.verifier.verify_pair",
        lambda *_a, **_k: Verification(status="confirmed", reason="shown"),
    )
    pair = sentinel.analyze(
        _clean_overlap_repo(tmp_path), "main", ["near-a", "near-b"],
        verify=True, verify_allowed=True,
    ).pairs[0]
    assert pair.model_level == "medium"
    assert pair.verification is not None and pair.verification.status == "confirmed"
    assert pair.level == "high" and pair.score == 90.0
    assert pair.score_note == sentinel.CONFIRMED_NOTE
    assert pair.blended_score is not None and pair.blended_score >= 70


def test_clean_merge_cap_still_yields_to_a_confirmed_branch_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "verify_allowed_roots", str(tmp_path))
    monkeypatch.setattr(
        "app.services.branch_tests.check_pair",
        lambda *_a, **_k: BranchTests(status="confirmed"),
    )
    pair = sentinel.analyze(
        _clean_overlap_repo(tmp_path), "main", ["near-a", "near-b"],
        verify=True, verify_allowed=True,
    ).pairs[0]
    assert pair.textual_conflict is False
    assert pair.fallback_reason == "no_api_key" and pair.score_source == "git"
    assert pair.git_score is not None and pair.git_score > 69
    assert pair.branch_tests is not None and pair.branch_tests.status == "confirmed"
    assert pair.level == "high" and pair.score == 90.0
    assert pair.score_note == sentinel.BRANCH_TEST_NOTE
    assert pair.blended_score == pair.git_score
