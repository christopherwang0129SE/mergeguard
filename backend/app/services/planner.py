"""Planner agent: suggest the safest merge order for in-flight branches.

Given a Sentinel ``AnalyzeResponse``, the Planner:
1. Builds a conflict-risk graph (branches = nodes, pairs = weighted edges).
2. Partitions branches into independent *zones* — groups that share no
   medium/high-risk pair.  Branches in different zones may be merged or
   worked on in parallel without conflict risk.
3. Within each zone, ranks branches by descending total risk so the most
   dangerous one merges first (while the base is still clean).
4. Returns a ``MergePlan`` with the ordered list, zone membership, and a
   plain-English rationale for each branch.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Literal

from app.schemas import AnalyzeResponse, PairRisk

# Risk levels as numeric weights used for graph edges.
_LEVEL_WEIGHT: dict[str, float] = {
    "none": 0.0,
    "low": 1.0,
    "medium": 5.0,
    "high": 10.0,
}

MergeStepStatus = Literal["safe", "caution", "danger"]


class MergeStep:
    """One branch in the proposed merge order."""

    def __init__(
        self,
        branch: str,
        position: int,
        zone: int,
        risk_score: float,
        status: MergeStepStatus,
        conflicts_with: list[str],
        rationale: str,
    ) -> None:
        self.branch = branch
        self.position = position
        self.zone = zone
        self.risk_score = risk_score
        self.status = status
        self.conflicts_with = conflicts_with
        self.rationale = rationale


class MergePlan:
    """The full plan returned by the Planner."""

    def __init__(
        self,
        steps: list[MergeStep],
        zones: list[list[str]],
        summary: str,
        parallelisable: bool,
    ) -> None:
        self.steps = steps
        self.zones = zones
        self.summary = summary
        self.parallelisable = parallelisable


# ---------------------------------------------------------------------------
# Union-Find (for zone partitioning)
# ---------------------------------------------------------------------------


def _make_uf(nodes: list[str]) -> dict[str, str]:
    return {n: n for n in nodes}


def _find(uf: dict[str, str], x: str) -> str:
    while uf[x] != x:
        uf[x] = uf[uf[x]]  # path compression
        x = uf[x]
    return x


def _union(uf: dict[str, str], a: str, b: str) -> None:
    ra, rb = _find(uf, a), _find(uf, b)
    if ra != rb:
        uf[ra] = rb


# ---------------------------------------------------------------------------
# Core planning logic
# ---------------------------------------------------------------------------


def _total_risk(branch: str, pairs: list[PairRisk]) -> float:
    """Sum of edge weights for all pairs involving *branch*."""
    return sum(
        _LEVEL_WEIGHT.get(p.level, 0.0)
        for p in pairs
        if p.branch_a == branch or p.branch_b == branch
    )


def _conflicts_with(branch: str, pairs: list[PairRisk]) -> list[str]:
    """Other branches that share a medium/high pair with *branch*."""
    result = []
    for p in pairs:
        if p.level not in ("medium", "high"):
            continue
        if p.branch_a == branch:
            result.append(p.branch_b)
        elif p.branch_b == branch:
            result.append(p.branch_a)
    return sorted(set(result))


def _status_for(risk_score: float, conflicts: list[str]) -> MergeStepStatus:
    if conflicts:
        return "danger" if risk_score >= _LEVEL_WEIGHT["high"] else "caution"
    return "safe"


def _build_rationale(
    branch: str,
    position: int,
    zone: int,
    risk_score: float,
    conflicts: list[str],
    same_zone_branches: list[str],
) -> str:
    others = [b for b in same_zone_branches if b != branch]
    if not conflicts:
        if not others:
            return (
                f"No conflicts detected. {branch!r} may be merged at any time."
            )
        return (
            f"Merge {branch!r} at step {position} before "
            + ", ".join(repr(b) for b in others)
            + " to keep risk low (no medium/high conflicts)."
        )
    conflict_str = ", ".join(repr(c) for c in conflicts)
    return (
        f"Merge {branch!r} at step {position} (zone {zone}). "
        f"It conflicts with {conflict_str} "
        f"(cumulative risk score {risk_score:.0f}). "
        "Landing it on a clean base reduces the chance of a semantic conflict."
    )


def plan(analysis: AnalyzeResponse) -> MergePlan:
    """Derive a ``MergePlan`` from a Sentinel ``AnalyzeResponse``."""
    branches = list(analysis.branches)
    pairs = analysis.pairs

    if not branches:
        return MergePlan(
            steps=[],
            zones=[],
            summary="No branches to plan.",
            parallelisable=False,
        )

    # --- Step 1: build union-find zones ---
    # Branches connected by a medium/high-risk pair belong to the same zone.
    uf = _make_uf(branches)
    for p in pairs:
        if p.level in ("medium", "high"):
            if p.branch_a in uf and p.branch_b in uf:
                _union(uf, p.branch_a, p.branch_b)

    # Collect zones: {root -> [branch, ...]}
    zone_map: dict[str, list[str]] = defaultdict(list)
    for b in branches:
        zone_map[_find(uf, b)].append(b)

    zones: list[list[str]] = []
    branch_to_zone: dict[str, int] = {}
    for i, members in enumerate(zone_map.values()):
        # Within a zone, sort by descending total risk (most dangerous first)
        members_sorted = sorted(
            members, key=lambda b: _total_risk(b, pairs), reverse=True
        )
        zones.append(members_sorted)
        for b in members_sorted:
            branch_to_zone[b] = i

    # --- Step 2: order zones ---
    # Zones with more branches or higher total risk go first.
    def _zone_risk(zone: list[str]) -> float:
        return sum(_total_risk(b, pairs) for b in zone)

    zones.sort(key=lambda z: (_zone_risk(z), len(z)), reverse=True)

    # Rebuild branch_to_zone with final zone indices
    for i, zone in enumerate(zones):
        for b in zone:
            branch_to_zone[b] = i

    # --- Step 3: build steps ---
    steps: list[MergeStep] = []
    position = 1
    for zone_idx, zone in enumerate(zones):
        same_zone = zone[:]
        for branch in zone:
            risk = _total_risk(branch, pairs)
            conflicts = _conflicts_with(branch, pairs)
            status = _status_for(risk, conflicts)
            rationale = _build_rationale(
                branch, position, zone_idx + 1, risk, conflicts, same_zone
            )
            steps.append(
                MergeStep(
                    branch=branch,
                    position=position,
                    zone=zone_idx + 1,
                    risk_score=round(risk, 1),
                    status=status,
                    conflicts_with=conflicts,
                    rationale=rationale,
                )
            )
            position += 1

    # --- Step 4: summary ---
    n_zones = len(zones)
    n_danger = sum(1 for s in steps if s.status == "danger")
    n_caution = sum(1 for s in steps if s.status == "caution")
    parallelisable = n_zones > 1

    if n_danger:
        summary = (
            f"{len(branches)} branch(es) across {n_zones} zone(s). "
            f"{n_danger} require careful sequencing (danger). "
            + ("Zones may be worked in parallel." if parallelisable else "")
        )
    elif n_caution:
        summary = (
            f"{len(branches)} branch(es) across {n_zones} zone(s). "
            f"{n_caution} need attention (caution). "
            + ("Zones may be worked in parallel." if parallelisable else "")
        )
    else:
        summary = (
            f"{len(branches)} branch(es), all low/no risk. "
            + (
                f"Split across {n_zones} independent zones — safe to merge in parallel."
                if parallelisable
                else "Safe to merge in any order."
            )
        )

    return MergePlan(
        steps=steps,
        zones=zones,
        summary=summary,
        parallelisable=parallelisable,
    )
