"""End-to-end test: build a real git repo with conflicting branches."""
from __future__ import annotations

from pathlib import Path

from app.schemas import BranchDiff, ChangedFile
from app.services import sentinel

_SHARED = "but no overlapping regions — merges cleanly today, but coordinate before divergence grows."
_OVERLAP = "changed in overlapping regions by both branches — a textual merge conflict is likely."


def _diff(branch: str, files: list[tuple[str, list[tuple[int, int]]]]) -> BranchDiff:
    return BranchDiff(
        branch=branch,
        base="main",
        merge_base="abc",
        ahead=1,
        files=[
            ChangedFile(path=path, added_lines=1, removed_lines=0, hunks=hunks)
            for path, hunks in files
        ],
    )


def test_overlap_is_high_risk(repo: Path) -> None:
    res = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"])
    pair = {frozenset((p.branch_a, p.branch_b)): p for p in res.pairs}

    ab = pair[frozenset(("feat-a", "feat-b"))]
    assert ab.overlapping_files == 1
    assert ab.level in ("medium", "high")
    assert ab.score >= 40
    assert ab.rationale == f"1 file {_OVERLAP}"
    assert ab.git_rationale == ab.rationale

    # a and c share app.py but edit far-apart lines -> no overlap.
    ac = pair[frozenset(("feat-a", "feat-c"))]
    assert ac.shared_files == 1
    assert ac.overlapping_files == 0
    assert ac.score < ab.score
    assert ac.rationale == f"1 shared file {_SHARED}"
    assert ac.git_rationale == ac.rationale


def test_git_rationale_pluralizes_shared_and_overlapping_files() -> None:
    one_shared = sentinel._score_pair(
        _diff("a", [("app.py", [(1, 2)])]),
        _diff("b", [("app.py", [(10, 12)])]),
    )
    two_shared = sentinel._score_pair(
        _diff("a", [("app.py", [(1, 2)]), ("lib.py", [(1, 1)])]),
        _diff("b", [("app.py", [(10, 12)]), ("lib.py", [(20, 21)])]),
    )
    one_overlap = sentinel._score_pair(
        _diff("a", [("app.py", [(1, 3)])]),
        _diff("b", [("app.py", [(2, 4)])]),
    )
    two_overlap = sentinel._score_pair(
        _diff("a", [("app.py", [(1, 3)]), ("lib.py", [(1, 2)])]),
        _diff("b", [("app.py", [(2, 4)]), ("lib.py", [(1, 2)])]),
    )
    neither = sentinel._score_pair(
        _diff("a", [("a.py", [(1, 1)])]),
        _diff("b", [("b.py", [(1, 1)])]),
    )

    assert one_shared.rationale == f"1 shared file {_SHARED}"
    assert two_shared.rationale == f"2 shared files {_SHARED}"
    assert one_overlap.rationale == f"1 file {_OVERLAP}"
    assert two_overlap.rationale == f"2 files {_OVERLAP}"
    assert neither.rationale == "No shared files — these branches are independent."
    for pair in (one_shared, two_shared, one_overlap, two_overlap, neither):
        assert pair.git_rationale == pair.rationale
        assert "file(s)" not in pair.rationale


def test_highest_risk_pair_first(repo: Path) -> None:
    res = sentinel.analyze(repo, "main", ["feat-a", "feat-b", "feat-c"])
    assert {res.pairs[0].branch_a, res.pairs[0].branch_b} == {"feat-a", "feat-b"}
