#!/usr/bin/env python3
"""Mine broke/clean pairs from first-parent history.

    python backend/scripts/bench/mine.py --repo PATH --branch main \\
        --owner-name owner/name --max-commits 1500 --out cases.jsonl --neg-ratio 1
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_LOG_FMT = "%H%x1f%P%x1f%ct%x1f%s%x1f%b%x1e"
_DAY = 86400

_MERGE_PR_RE = re.compile(r"^Merge pull request #(\d+)\b")
_TRAILING_PR_RE = re.compile(r"\(#(\d+)\)\s*$")
_REVERT_SHA_RE = re.compile(r"This reverts commit ([0-9a-fA-F]{7,40})")
_REVERT_INNER_PR_RE = re.compile(r'^Revert ".*\(#(\d+)\)"')
_PR_RE = re.compile(r"#(\d+)")
# Letter boundary so "prefix" and "debug" are not fix words. "fixed" still matches.
_FIX_WORD_RE = re.compile(
    r"(?<![A-Za-z])(?:fix|bug|regression|hotfix|broken|repair|correct|restore)|🐛|🚑",
    re.IGNORECASE,
)
_FOLLOWUP_DAYS = 3
_FOLLOWUP_GAP = 15
_MERGE_FIX_RE = re.compile(
    r"fix(?:ed)? (?:the )?merge"
    r"|merge (?:conflict|fix|skew)"
    r"|semantic conflict"
    r"|broken (?:by|after) (?:merge|#\d+)"
    r"|fix .*after merg"
    r"|fix (?:the )?(?:build|ci|tests?) (?:after|broken by|from|for) #\d+"
    r"|broke(?:n)? by #\d+",
    re.IGNORECASE | re.DOTALL,
)
_CONF_RANK = {"revert": 4, "explicit-merge-fix": 3, "medium": 2, "weak": 1, "none": 0}
_CODE_SUFFIXES = (".py", ".pyi", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx")
_SKIP_DIRS = frozenset({"docs", "doc", ".github", "examples"})
# Changelog names only. ``prepare_release.py`` is code and must still count.
_NOTES_STEMS = frozenset(
    {
        "changelog",
        "change-log",
        "change_log",
        "changes",
        "history",
        "news",
        "releases",
        "release-notes",
        "release_notes",
        "releasenotes",
        "release-note",
        "release_note",
    }
)
PrInfo = Callable[[str, str, int], dict | None]


@dataclass
class Change:
    sha: str
    parent: str
    kind: str
    pr: int | None
    time: int
    subject: str
    files: set[str]
    body: str = ""
    second_parent: str = ""


@dataclass
class FixEvent:
    change: Change
    kind: str
    reverted_sha: str | None
    prs: list[int] = field(default_factory=list)


@dataclass
class Case:
    case_id: str
    repo: str
    a: dict
    b: dict
    label: str
    gt_kind: str
    confidence: str
    fix_sha: str | None
    fix_subject: str | None
    pair_link: str
    parallel_evidence: str


def _git_proc(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-pager", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git(repo: Path, *args: str) -> str:
    proc = _git_proc(repo, *args)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"git {args[0]} failed"
        raise RuntimeError(detail)
    return proc.stdout


def is_code_file(path: str) -> bool:
    """True for source files that count toward A/B/F overlap."""
    parts = [part for part in path.replace("\\", "/").split("/") if part not in {"", "."}]
    if not parts:
        return False
    if any(part in _SKIP_DIRS for part in parts[:-1]):
        return False
    name = parts[-1].lower()
    if not name.endswith(_CODE_SUFFIXES):
        return False
    stem = name.rsplit(".", 1)[0]
    return stem not in _NOTES_STEMS


def code_files(paths: set[str]) -> set[str]:
    return {path for path in paths if is_code_file(path)}


def _unix_time(value: object) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _gh_env() -> dict[str, str]:
    """``gh`` colorizes JSON when ``CLICOLOR_FORCE`` is set, even on a pipe."""
    env = os.environ.copy()
    env.pop("CLICOLOR_FORCE", None)
    env.pop("FORCE_COLOR", None)
    env["CLICOLOR"] = "0"
    env["NO_COLOR"] = "1"
    return env


def _parse_pr_payload(text: str) -> dict | None:
    plain = _ANSI_RE.sub("", text).strip()
    if not plain:
        return None
    try:
        payload = json.loads(plain)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    created = payload.get("created_at")
    if not isinstance(created, str) or not created:
        return None
    merged = payload.get("merged_at")
    if not isinstance(merged, str):
        merged = None
    return {"created_at": created, "merged_at": merged}


def github_pr_info(owner: str, name: str, pr: int) -> dict | None:
    """Read-only GET of one pull request. None when the lookup fails."""
    proc = subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "GET",
            f"repos/{owner}/{name}/pulls/{pr}",
            "--jq",
            "{created_at: .created_at, merged_at: .merged_at}",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_gh_env(),
    )
    if proc.returncode != 0:
        return None
    return _parse_pr_payload(proc.stdout)


def pr_cache_path(out: Path) -> Path:
    return out.with_name(out.name + ".pr-cache.json")


def cached_pr_info(cache_path: Path, fetch: PrInfo = github_pr_info) -> PrInfo:
    """Disk-backed ``pr_info`` that calls ``fetch`` at most once per PR."""
    cache: dict[str, dict | None] = {}
    if cache_path.is_file():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            cache = loaded

    def pr_info(owner: str, name: str, pr: int) -> dict | None:
        key = f"{owner}/{name}#{int(pr)}"
        if key in cache:
            value = cache[key]
            return value if isinstance(value, dict) else None
        value = fetch(owner, name, int(pr))
        cache[key] = value
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
        return value

    return pr_info


def _pr_in_subject(subject: str) -> int | None:
    match = _MERGE_PR_RE.match(subject) or _TRAILING_PR_RE.search(subject)
    return int(match.group(1)) if match else None


def _kind(subject: str, parent_count: int) -> tuple[str, int | None]:
    pr = _pr_in_subject(subject)
    if parent_count > 1 or _MERGE_PR_RE.match(subject):
        return "merge", pr
    if pr is not None:
        return "squash", pr
    return "direct", None


def _files(repo: Path, parent: str, sha: str) -> set[str]:
    args = (
        ["diff", "--name-only", "-z", parent, sha]
        if parent
        else ["diff-tree", "--root", "--name-only", "--no-commit-id", "-r", "-z", sha]
    )
    return {name for name in _git(repo, *args).split("\0") if name}


def integrated_changes(repo: Path, branch: str, max_commits: int) -> list[Change]:
    """First-parent history of ``branch``, oldest commit first."""
    repo = Path(repo)
    if max_commits <= 0:
        return []
    raw = _git(
        repo, "log", "--first-parent", "-n", str(max_commits), f"--format={_LOG_FMT}", branch, "--"
    )
    changes: list[Change] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) < 4:
            continue
        sha, parents_raw, committed, subject, *rest = parts
        parents = parents_raw.split()
        parent = parents[0] if parents else ""
        second_parent = parents[1] if len(parents) > 1 else ""
        kind, pr = _kind(subject, len(parents))
        changes.append(
            Change(
                sha,
                parent,
                kind,
                pr,
                int(committed),
                subject,
                _files(repo, parent, sha),
                "\x1f".join(rest),
                second_parent,
            )
        )
    changes.reverse()
    return changes


def _referenced_prs(text: str) -> list[int]:
    return list(dict.fromkeys(int(match.group(1)) for match in _PR_RE.finditer(text)))


def _revert_target(subject: str) -> str | None:
    if not subject.startswith('Revert "'):
        return None
    quoted = subject[len('Revert "') :]
    if quoted.endswith('"'):
        return quoted[:-1]
    return None


def _revert_inner_pr(subject: str) -> int | None:
    """PR named inside ``Revert "... (#123)"``, not the revert commit's own trailing PR."""
    match = _REVERT_INNER_PR_RE.match(subject)
    return int(match.group(1)) if match else None


def _is_ancestor(
    repo: Path, ancestor: str, descendant: str, cache: dict[tuple[str, str], bool]
) -> bool:
    key = (ancestor, descendant)
    if key not in cache:
        proc = _git_proc(repo, "merge-base", "--is-ancestor", ancestor, descendant)
        cache[key] = proc.returncode == 0
    return cache[key]


def _resolve_commit(repo: Path, sha: str) -> str | None:
    proc = _git_proc(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if proc.returncode != 0:
        return None
    resolved = proc.stdout.strip()
    return resolved or None


def _containing_change(
    repo: Path,
    sha: str,
    changes: list[Change],
    cache: dict[tuple[str, str], bool],
) -> Change | None:
    """First-parent change that integrated ``sha``.

    ``sha`` is an ancestor of that change and not of its first parent, so a
    side-branch commit maps to the merge that brought it onto the branch.
    A commit already on the first-parent line maps to itself.
    """
    if not changes:
        return None
    full = _resolve_commit(repo, sha)
    if full is None or not _is_ancestor(repo, full, changes[-1].sha, cache):
        return None
    lo, hi = 0, len(changes) - 1
    found: int | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if _is_ancestor(repo, full, changes[mid].sha, cache):
            found = mid
            hi = mid - 1
        else:
            lo = mid + 1
    if found is None:
        return None
    change = changes[found]
    if change.parent and _is_ancestor(repo, full, change.parent, cache):
        return None
    return change


def find_fix_events(repo: Path, changes: list[Change]) -> list[FixEvent]:
    """Reverts, merge-fix messages, then fix-word follow-ups."""
    del repo  # commit bodies were stored while reading this repo
    events: list[FixEvent] = []
    for change in changes:
        text = f"{change.subject}\n{change.body}"
        prs = _referenced_prs(text)
        sha_match = _REVERT_SHA_RE.search(change.body)
        if sha_match or change.subject.startswith('Revert "'):
            reverted = sha_match.group(1) if sha_match else None
            events.append(FixEvent(change, "revert", reverted, prs))
            continue
        if _MERGE_FIX_RE.search(text):
            events.append(FixEvent(change, "merge-fix", None, prs))
            continue
        if _FIX_WORD_RE.search(change.subject):
            events.append(FixEvent(change, "followup-fix", None, prs))
    return events


def _as_dict(change: Change) -> dict:
    return {
        "sha": change.sha,
        "parent": change.parent,
        "kind": change.kind,
        "pr": change.pr,
        "time": change.time,
        "subject": change.subject,
        "files": sorted(change.files),
    }


def _sha_match(left: str, right: str) -> bool:
    a, b = left.lower(), right.lower()
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def _case(
    repo_name: str,
    earlier: Change,
    later: Change,
    label: str,
    gt_kind: str,
    confidence: str,
    fix_sha: str | None,
    fix_subject: str | None,
    pair_link: str,
    parallel_evidence: str,
) -> Case:
    return Case(
        case_id=f"{repo_name}-{earlier.sha[:8]}-{later.sha[:8]}",
        repo=repo_name,
        a=_as_dict(earlier),
        b=_as_dict(later),
        label=label,
        gt_kind=gt_kind,
        confidence=confidence,
        fix_sha=fix_sha,
        fix_subject=fix_subject,
        pair_link=pair_link,
        parallel_evidence=parallel_evidence,
    )


def _confidence(fix: FixEvent, how: str, earlier: Change, later: Change) -> str:
    if fix.kind == "revert" and how in {"sha", "mapped", "pr"}:
        return "revert"
    if fix.kind == "merge-fix" and how == "pr":
        return "explicit-merge-fix"
    if fix.kind == "followup-fix":
        named = {pr for pr in (earlier.pr, later.pr) if pr is not None}
        if named.intersection(fix.prs):
            return "medium"
    return "weak"


def mine_cases(
    repo: Path,
    changes: list[Change],
    fixes: list[FixEvent],
    max_days: int = 14,
    max_gap_commits: int = 40,
    pair_days: int = 7,
    pair_gap: int = 30,
    neg_ratio: float = 1.0,
    seed: int = 0,
    drops: Counter[str] | None = None,
    pr_info: PrInfo | None = None,
    owner: str | None = None,
    name: str | None = None,
) -> list[Case]:
    """Positive fix pairs, then ``round(neg_ratio * positives)`` clean pairs."""
    repo_name = Path(repo).resolve().name
    index = {change.sha: i for i, change in enumerate(changes)}
    if drops is None:
        drops = Counter()
    ancestor_cache: dict[tuple[str, str], bool] = {}
    merge_base_cache: dict[tuple[str, str], str | None] = {}
    pr_cache: dict[tuple[str, str, int], dict | None] = {}

    def lookup_pr(pr: int) -> dict | None:
        if pr_info is None or not owner or not name:
            return None
        key = (owner, name, int(pr))
        if key not in pr_cache:
            pr_cache[key] = pr_info(owner, name, int(pr))
        return pr_cache[key]

    def merge_base(left: str, right: str) -> str | None:
        key = (left, right)
        if key not in merge_base_cache:
            proc = _git_proc(repo, "merge-base", left, right)
            if proc.returncode != 0:
                merge_base_cache[key] = None
            else:
                merge_base_cache[key] = proc.stdout.strip() or None
        return merge_base_cache[key]

    def parallel_evidence(earlier: Change, later: Change) -> str | None:
        """How ``later`` (B) was in progress before ``earlier`` (A) landed.

        Direct commits have no PR and are not eligible. A merge is parallel
        when ``git merge-base <A> <B's second parent>`` does not contain A.
        A squash is parallel when B's PR ``created_at`` is before A's time.
        """
        if later.kind == "merge":
            if not later.second_parent:
                return None
            base = merge_base(earlier.sha, later.second_parent)
            if base is None:
                return "merge-topology"
            if _sha_match(base, earlier.sha) or _is_ancestor(repo, earlier.sha, base, ancestor_cache):
                return None
            return "merge-topology"
        if later.kind == "squash" and later.pr is not None:
            info = lookup_pr(later.pr)
            if not info:
                return None
            created = _unix_time(info.get("created_at"))
            if created is not None and created < earlier.time:
                return "pr-created-before-A"
            return None
        return None

    def before(later: Change, days: int, gap: int) -> list[Change]:
        end = index[later.sha]
        return [
            change
            for change in changes[max(0, end - gap) : end]
            if 0 <= later.time - change.time <= days * _DAY
        ]

    def in_prior(change: Change, later: Change, days: int, gap: int) -> bool:
        end = index[later.sha]
        at = index.get(change.sha)
        if at is None or at < max(0, end - gap) or at >= end:
            return False
        delta = later.time - change.time
        return 0 <= delta <= days * _DAY

    def pick_broken(fix: FixEvent) -> tuple[Change | None, str]:
        if fix.kind == "followup-fix":
            days, gap = _FOLLOWUP_DAYS, _FOLLOWUP_GAP
        else:
            days, gap = max_days, max_gap_commits
        prior = before(fix.change, days, gap)
        if fix.kind == "revert":
            shas = _REVERT_SHA_RE.findall(fix.change.body)
            if not shas and fix.reverted_sha:
                shas = [fix.reverted_sha]
            for sha in shas:
                for change in reversed(prior):
                    if _sha_match(sha, change.sha):
                        return change, "sha"
            for sha in shas:
                mapped = _containing_change(repo, sha, changes, ancestor_cache)
                if mapped is not None and in_prior(mapped, fix.change, days, gap):
                    return mapped, "mapped"
            pr = _revert_inner_pr(fix.change.subject)
            if pr is not None:
                for change in reversed(prior):
                    if change.pr == pr:
                        return change, "pr"
            quoted = _revert_target(fix.change.subject)
            if quoted is not None:
                for change in reversed(prior):
                    if change.subject == quoted:
                        return change, "subject"
            return None, ""
        if fix.kind == "merge-fix":
            for change in reversed(prior):
                if change.pr is not None and change.pr in fix.prs:
                    return change, "pr"
        touched = code_files(fix.change.files)
        for change in reversed(prior):
            if code_files(change.files) & touched:
                return change, "files"
        return None, ""

    def pick_partner(
        broken: Change, fix_change: Change, *, require_both: bool
    ) -> tuple[Change | None, str | None]:
        best: Change | None = None
        best_key: tuple[int, int, int, int, int] | None = None
        best_link: str | None = None
        fix_code = code_files(fix_change.files)
        broken_code = code_files(broken.files)
        for change in before(broken, pair_days, pair_gap):
            change_code = code_files(change.files)
            if not change_code:
                continue
            shared = change_code & broken_code
            both = bool(fix_code & change_code) and bool(fix_code & broken_code)
            if shared and (both or not require_both):
                link = "shared-file"
            elif both:
                link = "fix-touches-both"
            else:
                continue
            if fix_change.sha in {change.sha, broken.sha}:
                continue
            key = (
                1 if link == "shared-file" else 0,
                len(shared & fix_code),
                len(shared),
                len(change_code & fix_code),
                index[change.sha],
            )
            if best_key is None or key > best_key:
                best, best_key, best_link = change, key, link
        return best, best_link

    slots: dict[tuple[str, str], Case] = {}
    order: list[tuple[str, str]] = []
    for fix in fixes:
        if not code_files(fix.change.files):
            drops["no code files"] += 1
            continue
        broken, how = pick_broken(fix)
        if broken is None:
            drops["no B found"] += 1
            continue
        if not code_files(broken.files):
            drops["no code files"] += 1
            continue
        if fix.change.sha == broken.sha:
            drops["no B found"] += 1
            continue
        window = before(broken, pair_days, pair_gap)
        if not window:
            drops["no A in window"] += 1
            continue
        partner, link = pick_partner(
            broken, fix.change, require_both=fix.kind == "followup-fix"
        )
        if partner is None or link is None:
            if not any(code_files(change.files) for change in window):
                drops["no code files"] += 1
            else:
                drops["no shared link"] += 1
            continue
        evidence = parallel_evidence(partner, broken)
        if evidence is None:
            drops["not parallel"] += 1
            continue
        key = (partner.sha, broken.sha)
        case = _case(
            repo_name,
            partner,
            broken,
            "broke",
            fix.kind,
            _confidence(fix, how, partner, broken),
            fix.change.sha,
            fix.change.subject,
            link,
            evidence,
        )
        if key in slots:
            if _CONF_RANK[case.confidence] > _CONF_RANK[slots[key].confidence]:
                slots[key] = case
            continue
        slots[key] = case
        order.append(key)
    positives = [slots[key] for key in order]

    def later_fix_touches(earlier: Change, later: Change) -> bool:
        """True when a fix within ``max_days`` touches code from A or B."""
        watched = code_files(earlier.files) | code_files(later.files)
        if not watched:
            return False
        later_index = index[later.sha]
        limit = max_days * _DAY
        for fix in fixes:
            fix_change = fix.change
            fix_index = index.get(fix_change.sha)
            if fix_index is None or fix_index <= later_index:
                continue
            delta = fix_change.time - later.time
            if delta < 0 or delta > limit:
                continue
            if code_files(fix_change.files) & watched:
                return True
        return False

    eligible: list[tuple[Change, Change, str]] = []
    for later in changes:
        if not code_files(later.files):
            continue
        for earlier in before(later, pair_days, pair_gap):
            if not (code_files(earlier.files) & code_files(later.files)):
                continue
            if (earlier.sha, later.sha) in slots:
                continue
            evidence = parallel_evidence(earlier, later)
            if evidence is None:
                continue
            if later_fix_touches(earlier, later):
                continue
            eligible.append((earlier, later, evidence))
    random.Random(seed).shuffle(eligible)
    count = max(0, round(neg_ratio * len(positives)))
    negatives = [
        _case(
            repo_name,
            earlier,
            later,
            "clean",
            "none",
            "none",
            None,
            None,
            "shared-file",
            evidence,
        )
        for earlier, later, evidence in eligible[:count]
    ]
    return positives + negatives


def _print_counts(cases: list[Case], fixes: list[FixEvent], drops: Counter[str]) -> None:
    kinds = Counter(fix.kind for fix in fixes)
    labels = Counter(case.label for case in cases)
    confidences = Counter(case.confidence for case in cases)
    print(f"fix events: {len(fixes)}")
    for kind in ("revert", "merge-fix", "followup-fix"):
        print(f"fix {kind}: {kinds.get(kind, 0)}")
    for reason in ("no B found", "no A in window", "no shared link", "not parallel", "no code files"):
        print(f"dropped {reason}: {drops.get(reason, 0)}")
    print(f"cases: {len(cases)}")
    for label in ("broke", "clean"):
        print(f"label {label}: {labels.get(label, 0)}")
    for confidence in ("revert", "explicit-merge-fix", "medium", "weak", "none"):
        print(f"confidence {confidence}: {confidences.get(confidence, 0)}")


def _owner_name(value: str) -> tuple[str, str]:
    owner, sep, name = value.partition("/")
    if not sep or "/" in name or not owner or not name:
        raise argparse.ArgumentTypeError("expected owner/name")
    return owner, name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--max-commits", type=int, default=1500)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--neg-ratio", type=float, default=1.0)
    parser.add_argument("--max-days", type=int, default=14)
    parser.add_argument("--max-gap-commits", type=int, default=40)
    parser.add_argument("--pair-days", type=int, default=7)
    parser.add_argument("--pair-gap", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--owner-name",
        type=_owner_name,
        default=None,
        help="GitHub owner/name used for read-only PR created_at lookups",
    )
    args = parser.parse_args(argv)

    changes = integrated_changes(args.repo, args.branch, args.max_commits)
    fixes = find_fix_events(args.repo, changes)
    drops: Counter[str] = Counter()
    owner = name = None
    info: PrInfo | None = None
    if args.owner_name is not None:
        owner, name = args.owner_name
        info = cached_pr_info(pr_cache_path(args.out))
    cases = mine_cases(
        args.repo,
        changes,
        fixes,
        max_days=args.max_days,
        max_gap_commits=args.max_gap_commits,
        pair_days=args.pair_days,
        pair_gap=args.pair_gap,
        neg_ratio=args.neg_ratio,
        seed=args.seed,
        drops=drops,
        pr_info=info,
        owner=owner,
        name=name,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")
    _print_counts(cases, fixes, drops)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
