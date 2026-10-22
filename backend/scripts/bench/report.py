#!/usr/bin/env python3
"""Confusion matrices for mined and hand-checked bench ground truth.

    python scripts/bench/report.py --results DIR [--results DIR2 ...] \\
        [--labels labels.csv ...] [--src-root DIR] --out-dir OUT
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

_PY, _TS = {".py", ".pyi"}, {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_SRC_EXT = {".ts", ".tsx", ".js", ".mjs"}
_STRONG = {"revert", "explicit-merge-fix", "medium"}
_RANK = {"revert": 4, "explicit-merge-fix": 3, "medium": 2, "weak": 1, "none": 0}
_SRC_LANG: dict[tuple[str, str], str] = {}
_HAND, _NON_OK = {"interaction", "unrelated", "unsure"}, ("replay_failed", "timeout", "error")
CSV_COLUMNS = [
    "case_id", "repo", "owner", "name", "language", "label", "gt_mined", "gt_hand",
    "hand_label", "note", "gt_kind", "confidence", "status", "level", "score", "score_source",
    "flagged", "confirmed", "verification_status", "verification_reason",
    "branch_tests_status", "branch_tests_reason", "a_sha", "a_subject", "a_pr", "a_url",
    "b_sha", "b_subject", "b_pr", "b_url", "fix_sha", "fix_subject", "fix_url",
    "textual_conflict", "cost", "seconds",
]
def load_results(dirs: list[Path]) -> list[dict]:
    ordered: dict[str, dict] = {}
    for directory in dirs:
        path = directory / "results.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("case_id"):
                ordered[str(row["case_id"])] = row
    return list(ordered.values())
def load_labels(paths: list[Path] | None) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in paths or []:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                case_id, label = (row.get("case_id") or "").strip(), (row.get("hand_label") or "").strip().lower()
                if case_id and label in _HAND:
                    out[case_id] = {"hand_label": label, "note": (row.get("note") or "").strip()}
    return out
def split_repo(repo: str) -> tuple[str, str]:
    text = str(repo or "").strip().removesuffix(".git")
    sep = "/" if "/" in text else "__" if "__" in text else ""
    if not sep:
        return "", text
    owner, name = text.split(sep, 1)
    return owner, name
def commit_url(repo: str, sha: object) -> str:
    owner, name, text = *split_repo(repo), str(sha or "").strip()
    return f"https://github.com/{owner}/{name}/commit/{text}" if owner and name and text else ""
def _side(row: dict, key: str) -> dict:
    value = row.get(key)
    return value if isinstance(value, dict) else {}
def _files(row: dict) -> list[str]:
    found: list[str] = []
    for key in ("a", "b"):
        files = _side(row, key).get("files") or []
        if isinstance(files, (list, tuple)):
            found.extend(item for item in files if isinstance(item, str))
    return found
def language_of(files: list[str]) -> str:
    counts: Counter[str] = Counter()
    for path in files:
        ext = Path(path).suffix.lower()
        if ext in _PY or ext in _TS:
            counts[ext] += 1
    if not counts:
        return "unknown"
    winner = min(counts, key=lambda ext: (-counts[ext], ext not in _PY, ext))
    return "python" if winner in _PY else "ts"
def _src_language(src_root: Path, repo: str) -> str:
    key = (str(src_root), repo)
    if key in _SRC_LANG:
        return _SRC_LANG[key]
    proc = subprocess.run(
        ["git", "-C", str(src_root / repo), "ls-files"],
        capture_output=True, text=True, check=False,
    )
    py = ts = 0
    if proc.returncode == 0:
        for name in proc.stdout.splitlines():
            ext = Path(name).suffix.lower()
            py += ext == ".py"
            ts += ext in _SRC_EXT
    _SRC_LANG[key] = "python" if py > ts else "ts"
    return _SRC_LANG[key]
def repo_languages(rows: list[dict], src_root: Path | None = None) -> dict[str, str]:
    if src_root is None:
        pooled: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            pooled[str(row.get("repo") or "")].extend(_files(row))
        return {repo: language_of(paths) for repo, paths in pooled.items()}
    langs: dict[str, str] = {}
    for row in rows:
        repo = str(row.get("repo") or "")
        if repo not in langs:
            langs[repo] = _src_language(src_root, repo)
    return langs
def _field(row: dict, key: str, name: str) -> str:
    block = row.get(key)
    return str(block.get(name) or "") if isinstance(block, dict) else ""
def _stored(row: dict, key: str) -> bool | None:
    return bool(row[key]) if key in row and row[key] is not None else None
def is_confirmed(row: dict) -> bool:
    stored = _stored(row, "confirmed")
    if stored is not None:
        return stored
    return _field(row, "verification", "status") == "confirmed" or _field(row, "branch_tests", "status") == "confirmed"
def is_flagged(row: dict) -> bool:
    # Same rule as scripts.bench.run: a textual conflict is not a semantic flag.
    # Records with no ``finding`` keep the stored (or derived) flagged bit.
    if row.get("finding") == "textual_conflict":
        return False
    stored = _stored(row, "flagged")
    if stored is None:
        return row.get("level") == "high" or is_confirmed(row)
    return stored
def _textual_category(row: dict) -> bool:
    """New records use ``finding``. Old records fall back to replay ``textual_conflict``."""
    if "finding" in row and row.get("finding") is not None:
        return row.get("finding") == "textual_conflict"
    return bool(row.get("textual_conflict"))
def mined_gt(row: dict) -> str | None:
    return row.get("label") if row.get("label") in {"broke", "clean"} else None
def hand_gt(row: dict, hand: dict | None) -> str | None:
    label, name = mined_gt(row), (hand or {}).get("hand_label") or ""
    if name == "unsure":
        return None
    if name == "interaction":
        return "broke"
    if name == "unrelated":
        return "clean" if label in {"broke", "clean"} else None
    return "clean" if label == "clean" else None
def _truth(row: dict, labels: dict, variant: str) -> str | None:
    return mined_gt(row) if variant == "mined" else hand_gt(row, labels.get(str(row.get("case_id") or "")))
def confusion(pairs: list[tuple[bool, str]]) -> dict:
    tp = fp = fn = tn = 0
    for predicted, truth in pairs:
        if truth == "broke":
            tp, fn = tp + predicted, fn + (not predicted)
        elif truth == "clean":
            fp, tn = fp + predicted, tn + (not predicted)

    def ratio(num: int, den: int) -> float | None:
        return None if den == 0 else num / den

    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)
    f1 = None
    if precision is not None and recall is not None and precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": tp + fp + fn + tn,
        "precision": precision, "recall": recall, "specificity": ratio(tn, tn + fp), "f1": f1,
    }
def _num(row: dict, key: str) -> float:
    try: return float(row.get(key) or 0)
    except (TypeError, ValueError): return 0.0
def _agg(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "total": 0.0, "mean": None, "median": None}
    return {"n": len(values), "total": sum(values), "mean": sum(values) / len(values), "median": float(statistics.median(values))}
def _groups(rows: list[dict], labels: dict, langs: dict[str, str], variant: str, definition: str) -> dict:
    buckets: dict[str, dict[str, list]] = {name: defaultdict(list) for name in ("by_repo", "by_language", "by_confidence")}
    overall: list[tuple[bool, str]] = []
    strong_repos: set[str] = set()
    clean_by_repo: dict[str, list] = defaultdict(list)
    for row in rows:
        if row.get("status") != "ok":
            continue
        truth = _truth(row, labels, variant)
        if truth is None:
            continue
        repo = str(row.get("repo") or "")
        pair = (is_flagged(row) if definition == "flagged" else is_confirmed(row), truth)
        overall.append(pair)
        buckets["by_repo"][repo].append(pair)
        buckets["by_language"][langs.get(repo, "unknown")].append(pair)
        conf = str(row.get("confidence") or "")
        buckets["by_confidence"][conf].append(pair)
        if truth == "broke" and conf in _STRONG:
            strong_repos.add(repo)
            buckets["by_confidence"]["strong"].append(pair)
        elif truth == "clean":
            clean_by_repo[repo].append(pair)
    for repo in sorted(strong_repos):
        buckets["by_confidence"]["strong"].extend(clean_by_repo.get(repo, ()))
    grouped = {name: {key: confusion(items) for key, items in sorted(group.items())} for name, group in buckets.items()}
    return {"overall": confusion(overall), **grouped}
def _non_ok(rows: list[dict]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        status, repo = str(row.get("status") or ""), str(row.get("repo") or "")
        if status == "ok":
            continue
        slot = counts.setdefault(repo, {name: 0 for name in (*_NON_OK, "other")})
        slot[status if status in slot else "other"] += 1
    return dict(sorted(counts.items()))
def highlights(rows: list[dict], labels: dict) -> dict:
    variant, catches, misses, alarms = "hand" if labels else "mined", [], [], []
    for row in rows:
        if row.get("status") != "ok":
            continue
        truth = _truth(row, labels, variant)
        if truth is None:
            continue
        repo = str(row.get("repo") or "")
        item = {
            "case_id": row.get("case_id"), "confidence": row.get("confidence"),
            "flagged": is_flagged(row), "confirmed": is_confirmed(row),
            "a_url": commit_url(repo, _side(row, "a").get("sha")),
            "b_url": commit_url(repo, _side(row, "b").get("sha")),
            "fix_url": commit_url(repo, row.get("fix_sha")),
            "verification_reason": _field(row, "verification", "reason"),
            "branch_tests_reason": _field(row, "branch_tests", "reason"),
        }
        if truth == "broke" and item["flagged"]:
            catches.append(item)
        elif truth == "broke":
            misses.append(item)
        elif item["flagged"]:
            alarms.append(item)
    rank = lambda item: _RANK.get(str(item.get("confidence") or ""), -1)
    catches.sort(key=lambda item: (not item["confirmed"], -rank(item), str(item["case_id"])))
    misses.sort(key=lambda item: (-rank(item), str(item["case_id"])))
    alarms.sort(key=lambda item: (not item["confirmed"], -rank(item), str(item["case_id"])))
    return {"gt": variant, "top_catches": catches, "worst_misses": misses, "false_alarms": alarms}
def build_summary(rows: list[dict], labels: dict, src_root: Path | None = None) -> dict:
    langs = repo_languages(rows, src_root)
    variants = {
        variant: {kind: _groups(rows, labels, langs, variant, kind) for kind in ("flagged", "confirmed")}
        for variant in ("mined", "hand")
    }
    status = Counter(str(row.get("status") or "") for row in rows)
    return {
        "n_cases": len(rows), "n_ok": status.get("ok", 0),
        "n_replay_failed": status.get("replay_failed", 0), "n_timeout": status.get("timeout", 0),
        "n_error": status.get("error", 0),
        "n_flagged": sum(map(is_flagged, rows)), "n_confirmed": sum(map(is_confirmed, rows)),
        "n_textual_conflicts": sum(map(_textual_category, rows)),
        "n_labels": len(labels), "non_ok": _non_ok(rows), "variants": variants,
        "cost": _agg([_num(row, "cost") for row in rows]),
        "seconds": _agg([_num(row, "seconds") for row in rows]),
        "highlights": highlights(rows, labels),
    }
def _cell(value: object) -> object:
    return ("true" if value else "false") if isinstance(value, bool) else ("" if value is None else value)
def csv_row(row: dict, labels: dict, langs: dict[str, str]) -> dict:
    repo, case_id = str(row.get("repo") or ""), str(row.get("case_id") or "")
    owner, name = split_repo(repo)
    hand, a, b = labels.get(case_id, {}), _side(row, "a"), _side(row, "b")
    ok = row.get("status") == "ok"
    flat = {
        "case_id": case_id, "repo": repo, "owner": owner, "name": name, "language": langs.get(repo, "unknown"),
        "label": row.get("label"), "gt_mined": mined_gt(row) or "", "gt_hand": hand_gt(row, hand) or "",
        "hand_label": hand.get("hand_label", ""), "note": hand.get("note", ""), "gt_kind": row.get("gt_kind"),
        "confidence": row.get("confidence"), "status": row.get("status"), "level": row.get("level"),
        "score": row.get("score"), "score_source": row.get("score_source"),
        "flagged": is_flagged(row) if ok else row.get("flagged"),
        "confirmed": is_confirmed(row) if ok else row.get("confirmed"),
        "verification_status": _field(row, "verification", "status"),
        "verification_reason": _field(row, "verification", "reason"),
        "branch_tests_status": _field(row, "branch_tests", "status"),
        "branch_tests_reason": _field(row, "branch_tests", "reason"),
        "a_sha": a.get("sha"), "a_subject": a.get("subject"), "a_pr": a.get("pr"), "a_url": commit_url(repo, a.get("sha")),
        "b_sha": b.get("sha"), "b_subject": b.get("subject"), "b_pr": b.get("pr"), "b_url": commit_url(repo, b.get("sha")),
        "fix_sha": row.get("fix_sha"), "fix_subject": row.get("fix_subject"), "fix_url": commit_url(repo, row.get("fix_sha")),
        "textual_conflict": row.get("textual_conflict"), "cost": row.get("cost"), "seconds": row.get("seconds"),
    }
    return {key: _cell(flat.get(key)) for key in CSV_COLUMNS}
def _fmt(value: object) -> str:
    text = "-" if value is None or value == "" else f"{value:.3f}" if isinstance(value, float) else str(value)
    return text.replace("|", "\\|")
def _table(block: dict) -> list[str]:
    lines = ["| slice | n | TP | FP | FN | TN | precision | recall | specificity | F1 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    shown = [("overall", block["overall"])]
    for title, key in (("repo", "by_repo"), ("language", "by_language"), ("confidence", "by_confidence")):
        shown.extend((f"{title} {name}", metrics) for name, metrics in block[key].items())
    fields = ("n", "tp", "fp", "fn", "tn", "precision", "recall", "specificity", "f1")
    for label, metrics in shown:
        lines.append("| " + " | ".join([_fmt(label), *(_fmt(metrics[field]) for field in fields)]) + " |")
    return lines
def _bullet(item: dict) -> str:
    tag = "confirmed" if item["confirmed"] else "flagged" if item["flagged"] else "not flagged"
    links = " ".join(f"[{name}]({item[key]})" for name, key in (("A", "a_url"), ("B", "b_url"), ("fix", "fix_url")) if item[key])
    ver, branch = item["verification_reason"] or "-", item["branch_tests_reason"] or "-"
    return f"- `{item['case_id']}` ({tag}, {item['confidence']}) {links}. verification: {ver}; branch tests: {branch}"
def render_markdown(summary: dict) -> str:
    cost, seconds, caught = summary["cost"], summary["seconds"], summary["highlights"]
    lines = [
        "# Bench report", "", "## Headline", "",
        f"{summary['n_cases']} cases, {summary['n_ok']} ok, {summary['n_replay_failed']} replay_failed, "
        f"{summary['n_timeout']} timeout, {summary['n_error']} error. "
        f"{summary['n_flagged']} flagged, {summary['n_confirmed']} confirmed. Cost {_fmt(cost['total'])}. "
        f"{summary['n_textual_conflicts']} textual conflicts (separate category).",
        "",
    ]
    for name, variant in (("Mined", "mined"), ("Hand", "hand")):
        cell = summary["variants"][variant]["flagged"]["overall"]
        lines.append(f"{name} flagged: TP {cell['tp']}, FP {cell['fp']}, FN {cell['fn']}, TN {cell['tn']}.")
    lines.extend([
        "",
        "The confidence strong slice includes broke cases with confidence revert, explicit-merge-fix, or medium, "
        "plus every clean case from those same repos.",
        "",
        f"{summary['n_cases']} cases. Cost {_fmt(cost['total'])} (mean {_fmt(cost['mean'])}, median {_fmt(cost['median'])}). "
        f"Seconds {_fmt(seconds['total'])} (mean {_fmt(seconds['mean'])}, median {_fmt(seconds['median'])}).",
        "", "## Non-ok statuses", "",
        "| repo | replay_failed | timeout | error | other |", "|---|---:|---:|---:|---:|",
    ])
    non_ok = summary["non_ok"] or {"-": {name: 0 for name in (*_NON_OK, "other")}}
    for repo, counts in non_ok.items():
        lines.append(f"| {repo} | {counts['replay_failed']} | {counts['timeout']} | {counts['error']} | {counts['other']} |")
    titles = (("mined", "Mined ground truth"), ("hand", "Hand-checked ground truth"))
    defs = (("flagged", "Flagged (high or test-confirmed)"), ("confirmed", "Confirmed (test-confirmed only)"))
    for variant, heading in titles:
        lines.extend(["", f"## {heading}", ""])
        for kind, sub in defs:
            lines.extend([f"### {sub}", "", *_table(summary["variants"][variant][kind]), ""])
    for heading, key, empty in (
        ("Top catches", "top_catches", "No true positives."),
        ("Worst misses", "worst_misses", "No false negatives."),
        ("False alarms", "false_alarms", "No false positives."),
    ):
        items = caught[key]
        lines.extend(["", f"## {heading}", "", f"Ground truth: {caught['gt']}.", "", *([_bullet(item) for item in items] or [empty])])
    return "\n".join(lines) + "\n"
def write_outputs(rows: list[dict], labels: dict, out_dir: Path, src_root: Path | None = None) -> dict:
    summary, langs = build_summary(rows, labels, src_root), repo_languages(rows, src_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out_dir / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(csv_row(row, labels, langs) for row in rows)
    return summary
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, action="append", required=True)
    parser.add_argument("--labels", type=Path, action="append", default=None)
    parser.add_argument("--src-root", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    rows, labels = load_results(args.results), load_labels(args.labels)
    write_outputs(rows, labels, args.out_dir, args.src_root)
    print(f"wrote {args.out_dir} ({len(rows)} cases)")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
