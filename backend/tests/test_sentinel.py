"""End-to-end test: build a real git repo with conflicting branches."""
from __future__ import annotations

from pathlib import Path

from app.services import sentinel


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
