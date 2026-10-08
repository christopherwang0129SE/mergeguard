#!/usr/bin/env python3
"""Reliability eval: run MergeGuard N times per case, fresh (no cache), and
tabulate the end-to-end verdicts.

    make eval-reliability            # N=5, stops before exceeding MAX_USD
    cd backend && PYTHONPATH=. python scripts/eval_reliability.py --runs 5

Needs NEBIUS_API_KEY in the environment (read by app.config; never printed).

Cases (local repos under demo/, built if missing):
- real-case-1: pymc-labs/daimon PR #14 x PR #17 (demo/replay_case_1.py);
  the PR pair is expected to be confirmed.
- checkout-demo: feat/discount-cap x feat/tax-before-discount expected
  confirmed; the docs/readme pairs are harmless.
- harmless-demo: every pair is harmless (demo/make_harmless_repo.py).
- harmless-demo-forced-verify: the same repo with verify-by-test forced on
  EVERY pair (normally harmless pairs never reach verify); --forced-runs runs.

A harmless pair must never be confirmed. Raw results go to
backend/.workspace/eval/<timestamp>/ (gitignored).
"""
from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
DEMO = ROOT / "demo"
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.services import git_service, sentinel, verifier  # noqa: E402

_is_high_risk = verifier.is_high_risk
_max_pairs = settings.verify_max_pairs


@dataclass
class Case:
    name: str
    repo: Path
    build: list[str]
    expect_confirmed: set[frozenset[str]]
    force_verify: bool = False  # verify every pair, whatever its score


CASES = [
    Case(
        "real-case-1",
        DEMO / "real-case-1",
        [sys.executable, str(DEMO / "replay_case_1.py")],
        {frozenset({"pr-14-sync-fixes", "pr-17-deploy-review-findings"})},
    ),
    Case(
        "checkout-demo",
        DEMO / "checkout-demo",
        [sys.executable, str(DEMO / "make_demo_repo.py")],
        {frozenset({"feat/discount-cap", "feat/tax-before-discount"})},
    ),
    Case("harmless-demo", DEMO / "harmless-demo",
         [sys.executable, str(DEMO / "make_harmless_repo.py")], set()),
    # Stress test of the verifier itself: generate and run tests for EVERY
    # harmless pair (normally they never reach verify), none may confirm.
    Case("harmless-demo-forced-verify", DEMO / "harmless-demo",
         [sys.executable, str(DEMO / "make_harmless_repo.py")], set(), force_verify=True),
]


@dataclass
class Row:
    label: str
    runs: int = 0
    pairs: int = 0
    verdicts: dict[str, int] = field(default_factory=dict)
    unsupported: int = 0
    times: list[float] = field(default_factory=list)
    tokens: int = 0
    usd: float = 0.0
    model_scores: list[float] = field(default_factory=list)

    def add(self, status: str) -> None:
        self.verdicts[status] = self.verdicts.get(status, 0) + 1


def _status(pair) -> str:
    return pair.verification.status if pair.verification else "not verified"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--max-usd", type=float, default=1.0)
    ap.add_argument("--cases", default=",".join(c.name for c in CASES))
    ap.add_argument("--forced-runs", type=int, default=3,
                    help="runs for the forced-verify stress case (6 verifies per run)")
    args = ap.parse_args()
    if not settings.nebius_api_key:
        print("NEBIUS_API_KEY is not set", file=sys.stderr)
        return 2

    out_dir = BACKEND / ".workspace" / "eval" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(args.cases.split(","))
    spent = 0.0
    rows: list[Row] = []
    stopped = None
    for case in [c for c in CASES if c.name in wanted]:
        if not case.repo.exists():
            subprocess.run(case.build, check=True)
        branches = git_service.list_branches(case.repo, exclude="main")
        target = Row(f"{case.name} (expected confirmed)")
        harmless = Row(f"{case.name} (harmless pairs)")
        if case.force_verify:
            verifier.is_high_risk = lambda _pair: True
            settings.verify_max_pairs = 100
        else:
            verifier.is_high_risk = _is_high_risk
            settings.verify_max_pairs = _max_pairs
        for i in range(1, (args.forced_runs if case.force_verify else args.runs) + 1):
            if spent >= args.max_usd:
                stopped = f"budget reached (${spent:.4f} >= ${args.max_usd}) before {case.name} run {i}"
                break
            started = time.monotonic()
            res = sentinel.analyze(case.repo, "main", branches, use_cache=False, verify=True,
                                   verify_allowed=True)
            elapsed = time.monotonic() - started
            usage = res.usage
            spent += usage.estimated_cost_usd if usage else 0.0
            (out_dir / f"{case.name}-run{i}.json").write_text(res.model_dump_json(indent=2))
            line = []
            for row in (target, harmless):
                row.runs += 1
                row.times.append(elapsed)
            target.tokens += usage.total_tokens if usage else 0
            target.usd += usage.estimated_cost_usd if usage else 0.0
            for pair in res.pairs:
                key = frozenset({pair.branch_a, pair.branch_b})
                row = target if key in case.expect_confirmed else harmless
                row.pairs += 1
                row.add(_status(pair))
                row.unsupported += bool(pair.unsupported_claims)
                if pair.model_score is not None:
                    row.model_scores.append(pair.model_score)
                line.append(f"{pair.branch_a} x {pair.branch_b}: {_status(pair)}"
                            f" (model {pair.model_score})")
            print(f"[{case.name} run {i}] {elapsed:.1f}s ${usage.estimated_cost_usd:.4f} | "
                  + "; ".join(line), flush=True)
        if not target.pairs:  # no expected-confirmed pair: the runs belong to the harmless row
            harmless.tokens, harmless.usd = target.tokens, target.usd
        rows += [r for r in (target, harmless) if r.pairs]
        if stopped:
            break

    header = ("| case | runs | pairs | confirmed | suspected | error | not verified | "
              "unsupported claims | median time (s) | total tokens | total $ |")
    lines = [header, "|" + "---|" * 11]
    for r in rows:
        v = r.verdicts
        shared = r.label.endswith("(harmless pairs)") and any(
            o.label.split(" ")[0] == r.label.split(" ")[0] and o is not r for o in rows
        )
        cost = ("(same runs)", "(same runs)") if shared else (f"{r.tokens:,}", f"{r.usd:.4f}")
        lines.append(
            f"| {r.label} | {r.runs} | {r.pairs} | {v.get('confirmed', 0)} | "
            f"{v.get('suspected', 0)} | {v.get('error', 0)} | "
            f"{v.get('not verified', 0) + v.get('skipped', 0)} | {r.unsupported} | "
            f"{statistics.median(r.times):.1f} | {cost[0]} | {cost[1]} |"
        )
    lines.append(f"\nTotal estimated spend: ${spent:.4f} (budget ${args.max_usd}).")
    if stopped:
        lines.append(f"Stopped early: {stopped}.")
    table = "\n".join(lines)
    print("\n" + table)
    (out_dir / "table.md").write_text(table + "\n")
    print(f"\nraw results: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
