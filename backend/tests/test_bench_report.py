"""Bench report: confusion matrices, hand labels, links, CSV."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest

from scripts.bench import report as report_mod
from scripts.bench.report import (
    CSV_COLUMNS,
    build_summary,
    commit_url,
    confusion,
    hand_gt,
    is_confirmed,
    is_flagged,
    language_of,
    load_labels,
    load_results,
    main,
    repo_languages,
)

_SHA_A = "a" * 40
_SHA_B = "b" * 40
_SHA_F = "f" * 40


def _row(
    case_id: str,
    *,
    label: str = "broke",
    status: str = "ok",
    repo: str = "psf__requests",
    confidence: str = "weak",
    flagged: bool | None = False,
    confirmed: bool | None = False,
    level: str = "low",
    files: list[str] | None = None,
    cost: float = 0.1,
    seconds: float = 2.0,
    verification: str = "skipped",
    branch: str = "passed",
    v_reason: str = "",
    b_reason: str = "",
    include_flags: bool = True,
) -> dict:
    row = {
        "case_id": case_id,
        "repo": repo,
        "label": label,
        "gt_kind": "merge-fix" if label == "broke" else "none",
        "confidence": confidence,
        "a": {"sha": _SHA_A, "subject": "change A", "pr": 11, "files": files or []},
        "b": {"sha": _SHA_B, "subject": "change B", "pr": 12, "files": []},
        "fix_sha": _SHA_F if label == "broke" else None,
        "fix_subject": "merge fix" if label == "broke" else None,
        "textual_conflict": False,
        "status": status,
        "level": level if status == "ok" else None,
        "score": 20.0 if status == "ok" else None,
        "score_source": "model" if status == "ok" else None,
        "verification": {"status": verification, "reason": v_reason},
        "branch_tests": {"status": branch, "reason": b_reason},
        "cost": cost,
        "seconds": seconds,
    }
    if include_flags and status == "ok":
        row["flagged"] = flagged
        row["confirmed"] = confirmed
    return row


def _write_jsonl(directory: Path, rows: list[dict], extra: str = "") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row) + "\n" for row in rows) + extra
    (directory / "results.jsonl").write_text(text, encoding="utf-8")


def test_confusion_zero_division() -> None:
    empty = confusion([])
    assert empty["tp"] == empty["fp"] == empty["fn"] == empty["tn"] == 0
    assert empty["precision"] is empty["recall"] is empty["specificity"] is empty["f1"] is None

    only_tn = confusion([(False, "clean")] * 3)
    assert only_tn["specificity"] == 1
    assert only_tn["precision"] is only_tn["recall"] is only_tn["f1"] is None

    both_zero = confusion([(True, "clean"), (False, "broke")])
    assert both_zero["precision"] == 0
    assert both_zero["recall"] == 0
    assert both_zero["specificity"] == 0
    assert both_zero["f1"] is None

    perfect = confusion([(True, "broke"), (True, "broke")])
    assert perfect["precision"] == perfect["recall"] == perfect["f1"] == 1
    assert perfect["specificity"] is None

    mixed = confusion([(True, "broke"), (True, "clean"), (False, "broke"), (False, "clean")])
    assert (mixed["tp"], mixed["fp"], mixed["fn"], mixed["tn"]) == (1, 1, 1, 1)
    assert mixed["precision"] == mixed["recall"] == mixed["specificity"] == mixed["f1"] == 0.5


def test_flag_derivation_when_booleans_absent() -> None:
    high = _row("h", level="high", include_flags=False)
    assert is_flagged(high) is True
    assert is_confirmed(high) is False
    tests = _row("t", include_flags=False, branch="confirmed", b_reason="regressed")
    assert is_confirmed(tests) is True
    assert is_flagged(tests) is True
    tree = _row("v", include_flags=False, verification="confirmed", v_reason="import cycle")
    assert is_confirmed(tree) is True


def test_hand_label_override_and_non_ok(tmp_path: Path) -> None:
    rows = [
        _row("b1", flagged=True, confirmed=True, confidence="weak", v_reason="broke import"),
        _row("b2", flagged=True, confirmed=False, confidence="medium"),
        _row("b3", flagged=True, confirmed=False, confidence="weak"),
        _row("b4", flagged=False, confirmed=False, confidence="revert"),
        _row("c1", label="clean", confidence="none"),
        _row("c2", label="clean", confidence="none", flagged=False),
        _row("x1", status="replay_failed", include_flags=False, cost=0, seconds=1),
        _row("x2", status="timeout", repo="fastapi__fastapi", include_flags=False, cost=0, seconds=9),
    ]
    # c2 is a second clean so specificity has a denominator after overrides.
    labels = {
        "b1": {"hand_label": "interaction", "note": "real"},
        "b2": {"hand_label": "unrelated", "note": "same file, no interaction"},
        "b3": {"hand_label": "unsure", "note": "maybe"},
        "c2": {"hand_label": "unsure", "note": "skip"},
    }
    assert hand_gt(rows[0], labels["b1"]) == "broke"
    assert hand_gt(rows[1], labels["b2"]) == "clean"
    assert hand_gt(rows[2], labels["b3"]) is None
    assert hand_gt(rows[3], None) is None
    assert hand_gt(rows[4], None) == "clean"
    assert hand_gt(_row("up", label="clean"), {"hand_label": "interaction"}) == "broke"

    summary = build_summary(rows, labels)
    mined = summary["variants"]["mined"]["flagged"]["overall"]
    # b1, b2, b3 true positives; b4 false negative; c1, c2 true negatives. x* excluded.
    assert (mined["tp"], mined["fp"], mined["fn"], mined["tn"], mined["n"]) == (3, 0, 1, 2, 6)
    hand = summary["variants"]["hand"]["flagged"]["overall"]
    # b1 TP; b2 unrelated -> FP; b3 unsure and b4 unlabeled dropped; c1 TN; c2 unsure dropped.
    assert (hand["tp"], hand["fp"], hand["fn"], hand["tn"], hand["n"]) == (1, 1, 0, 1, 3)
    assert hand["precision"] == hand["specificity"] == 0.5
    assert hand["recall"] == 1
    assert hand["f1"] == pytest.approx(2 / 3)

    confirmed = summary["variants"]["hand"]["confirmed"]["overall"]
    assert (confirmed["tp"], confirmed["fp"], confirmed["fn"], confirmed["tn"]) == (1, 0, 0, 2)
    assert summary["non_ok"]["psf__requests"]["replay_failed"] == 1
    assert summary["non_ok"]["fastapi__fastapi"]["timeout"] == 1
    assert summary["non_ok"]["psf__requests"]["error"] == 0
    assert summary["cost"]["n"] == 8
    assert summary["cost"]["total"] == pytest.approx(0.6)
    assert summary["seconds"]["median"] == pytest.approx(2.0)

    assert summary["highlights"]["gt"] == "hand"
    assert [item["case_id"] for item in summary["highlights"]["top_catches"]] == ["b1"]
    assert summary["highlights"]["top_catches"][0]["verification_reason"] == "broke import"
    assert [item["case_id"] for item in summary["highlights"]["false_alarms"]] == ["b2"]
    assert summary["highlights"]["worst_misses"] == []

    mined_only = build_summary(rows, {})
    assert mined_only["highlights"]["gt"] == "mined"
    assert mined_only["highlights"]["worst_misses"][0]["case_id"] == "b4"
    assert mined_only["highlights"]["worst_misses"][0]["confidence"] == "revert"


def test_language_majority_and_repo_pool() -> None:
    assert language_of(["a.py", "b.ts", "c.py"]) == "python"
    assert language_of(["a.ts", "b.tsx", "c.js"]) == "ts"
    assert language_of(["README.md"]) == "unknown"
    assert language_of(["a.py", "b.ts"]) == "python"
    rows = [
        _row("p1", repo="psf__requests", files=["src/a.py"]),
        _row("p2", repo="psf__requests", files=[]),
        _row("t1", repo="colinhacks__zod", files=["src/a.ts", "src/b.ts"]),
        _row("u1", repo="mystery__lib"),
    ]
    langs = repo_languages(rows)
    assert langs["psf__requests"] == "python"
    assert langs["colinhacks__zod"] == "ts"
    assert langs["mystery__lib"] == "unknown"
    summary = build_summary(rows, {})
    by_lang = summary["variants"]["mined"]["flagged"]["by_language"]
    assert set(by_lang) == {"python", "ts", "unknown"}
    assert by_lang["python"]["n"] == 2


def test_links_last_wins_and_csv(tmp_path: Path) -> None:
    assert commit_url("psf__requests", _SHA_A) == f"https://github.com/psf/requests/commit/{_SHA_A}"
    assert commit_url("encode/httpx", "abc") == "https://github.com/encode/httpx/commit/abc"
    assert commit_url("psf__requests", None) == ""
    assert commit_url("nosplit", _SHA_A) == ""

    first, second = tmp_path / "one", tmp_path / "two"
    _write_jsonl(first, [_row("dup", status="ok", flagged=True, confirmed=True)])
    _write_jsonl(
        second,
        [_row("dup", status="error", include_flags=False, cost=0, seconds=4)],
        extra='{"case_id": "partial"',
    )
    labels = tmp_path / "labels.csv"
    labels.write_text("case_id,hand_label,note\ndup,interaction,\"has, comma\"\n", encoding="utf-8")
    out = tmp_path / "out"
    assert main(["--results", str(first), "--results", str(second), "--labels", str(labels), "--out-dir", str(out)]) == 0

    loaded = load_results([first, second])
    assert len(loaded) == 1
    assert loaded[0]["status"] == "error"

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["n_cases"] == 1
    assert summary["variants"]["mined"]["flagged"]["overall"]["n"] == 0
    assert summary["non_ok"]["psf__requests"]["error"] == 1

    with (out / "results.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == CSV_COLUMNS
        body = list(reader)
    assert len(body) == 1
    assert body[0]["a_url"] == f"https://github.com/psf/requests/commit/{_SHA_A}"
    assert body[0]["b_url"] == f"https://github.com/psf/requests/commit/{_SHA_B}"
    assert body[0]["fix_url"] == f"https://github.com/psf/requests/commit/{_SHA_F}"
    assert body[0]["owner"] == "psf" and body[0]["name"] == "requests"
    assert body[0]["hand_label"] == "interaction"
    assert body[0]["note"] == "has, comma"
    assert body[0]["gt_hand"] == "broke"
    assert "textual_conflict" in body[0]
    text = (out / "summary.md").read_text(encoding="utf-8")
    assert "## Top catches" in text
    assert "## Worst misses" in text
    assert "## False alarms" in text
    assert "| slice | n | TP | FP | FN | TN |" in text


def test_highlight_order() -> None:
    rows = [
        _row("low-flag", flagged=True, confirmed=False, confidence="weak", v_reason="weak hit"),
        _row("conf", flagged=True, confirmed=True, confidence="medium", b_reason="test failed"),
        _row("miss-weak", flagged=False, confidence="weak"),
        _row("miss-revert", flagged=False, confidence="revert"),
        _row("fp-conf", label="clean", flagged=True, confirmed=True, confidence="none"),
        _row("fp-flag", label="clean", flagged=True, confirmed=False, confidence="none"),
        _row("tn", label="clean", flagged=False, confidence="none"),
    ]
    summary = build_summary(rows, {})
    assert [item["case_id"] for item in summary["highlights"]["top_catches"]] == ["conf", "low-flag"]
    assert [item["case_id"] for item in summary["highlights"]["worst_misses"]] == ["miss-revert", "miss-weak"]
    assert [item["case_id"] for item in summary["highlights"]["false_alarms"]] == ["fp-conf", "fp-flag"]
    caught = summary["highlights"]["top_catches"][0]
    assert caught["a_url"].endswith(_SHA_A)
    assert caught["branch_tests_reason"] == "test failed"


def _git_repo(path: Path, files: list[str], *, empty: bool = False) -> None:
    path.mkdir(parents=True, exist_ok=True)
    base = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*base, "init"], cwd=path, check=True, capture_output=True, text=True)
    if empty:
        subprocess.run(
            [*base, "commit", "--allow-empty", "-m", "empty", "--no-gpg-sign"],
            cwd=path, check=True, capture_output=True, text=True,
        )
        return
    for name in files:
        dest = path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("x\n", encoding="utf-8")
    subprocess.run([*base, "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        [*base, "commit", "-m", "init", "--no-gpg-sign"],
        cwd=path, check=True, capture_output=True, text=True,
    )


def test_src_root_language_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "src"
    _git_repo(root / "alpha__py", ["pkg/a.py", "pkg/b.py", "web/app.js"])
    _git_repo(root / "beta__ts", ["src/a.ts", "src/b.tsx", "src/c.js", "src/d.mjs"])
    for index in range(5):
        (root / "beta__ts" / f"untracked_{index}.py").write_text("x\n", encoding="utf-8")
    _git_repo(root / "tie__eq", ["a.py", "b.ts"])
    _git_repo(root / "empty__lib", [], empty=True)
    rows = [
        _row("p1", repo="alpha__py", files=["a.ts", "b.tsx", "c.js"]),
        _row("p2", repo="alpha__py", files=["d.ts"]),
        _row("t1", repo="beta__ts", files=["a.py", "b.py", "c.py"]),
        _row("e1", repo="tie__eq", files=["a.py", "b.py"]),
        _row("z1", repo="empty__lib"),
        _row("m1", repo="mystery__lib", files=["a.py"]),
    ]
    without = repo_languages(rows)
    assert without["alpha__py"] == "ts"
    assert without["beta__ts"] == "python"
    assert without["tie__eq"] == "python"
    assert without["empty__lib"] == "unknown"
    assert without["mystery__lib"] == "python"

    calls: list[list[str]] = []
    real_run = subprocess.run

    def counting(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and "ls-files" in cmd:
            calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(report_mod.subprocess, "run", counting)
    langs = repo_languages(rows, root)
    assert langs == {
        "alpha__py": "python",
        "beta__ts": "ts",
        "tie__eq": "ts",
        "empty__lib": "ts",
        "mystery__lib": "ts",
    }
    assert len(calls) == 5
    assert repo_languages(rows, root) == langs
    assert len(calls) == 5

    out = tmp_path / "out"
    _write_jsonl(tmp_path / "results", rows)
    assert main(["--results", str(tmp_path / "results"), "--src-root", str(root), "--out-dir", str(out)]) == 0
    with (out / "results.csv").open(newline="", encoding="utf-8") as handle:
        body = {row["case_id"]: row for row in csv.DictReader(handle)}
    assert body["p1"]["language"] == body["p2"]["language"] == "python"
    assert body["t1"]["language"] == "ts"
    assert body["e1"]["language"] == "ts"
    by_lang = build_summary(rows, {}, root)["variants"]["mined"]["flagged"]["by_language"]
    assert by_lang["python"]["n"] == 2
    assert by_lang["ts"]["n"] == 4
    assert len(calls) == 5


def test_strong_slice_includes_clean_cases_from_same_repos() -> None:
    rows = [
        _row("rev", repo="hot", confidence="revert", flagged=True, confirmed=True),
        _row("med", repo="hot", confidence="medium", flagged=False),
        _row("fix", repo="hot", confidence="explicit-merge-fix", flagged=True),
        _row("weak", repo="hot", confidence="weak", flagged=True),
        _row("hc1", repo="hot", label="clean", confidence="none", flagged=True),
        _row("hc2", repo="hot", label="clean", confidence="none", flagged=False),
        _row("cold-b", repo="cold", confidence="weak", flagged=False),
        _row("cold-c", repo="cold", label="clean", confidence="none", flagged=True),
        _row("solo", repo="solo", label="clean", confidence="none", flagged=False),
        _row("bad", repo="hot", status="replay_failed", confidence="revert", label="clean", include_flags=False),
    ]
    mined = build_summary(rows, {})["variants"]["mined"]
    strong = mined["flagged"]["by_confidence"]["strong"]
    assert (strong["tp"], strong["fp"], strong["fn"], strong["tn"], strong["n"]) == (2, 1, 1, 1, 5)
    medium = mined["flagged"]["by_confidence"]["medium"]
    assert (medium["tp"], medium["fp"], medium["fn"], medium["tn"], medium["n"]) == (0, 0, 1, 0, 1)
    assert mined["flagged"]["by_confidence"]["weak"]["n"] == 2
    assert mined["flagged"]["by_confidence"]["none"]["n"] == 4
    confirmed = mined["confirmed"]["by_confidence"]["strong"]
    assert (confirmed["tp"], confirmed["fp"], confirmed["fn"], confirmed["tn"]) == (1, 0, 2, 2)

    labels = {
        "rev": {"hand_label": "unrelated", "note": "not a break"},
        "med": {"hand_label": "unsure", "note": "drop"},
        "fix": {"hand_label": "interaction", "note": "real"},
        "weak": {"hand_label": "interaction", "note": "weak"},
        "hc1": {"hand_label": "unrelated", "note": "still clean"},
    }
    hand = build_summary(rows, labels)["variants"]["hand"]["flagged"]["by_confidence"]
    assert (hand["strong"]["tp"], hand["strong"]["fp"], hand["strong"]["fn"], hand["strong"]["tn"], hand["strong"]["n"]) == (
        1, 2, 0, 1, 4,
    )
    dropped = {
        "rev": {"hand_label": "unsure", "note": ""},
        "med": {"hand_label": "unsure", "note": ""},
        "fix": {"hand_label": "unsure", "note": ""},
    }
    assert "strong" not in build_summary(rows, dropped)["variants"]["hand"]["flagged"]["by_confidence"]


def test_headline_and_merged_label_files(tmp_path: Path) -> None:
    rows = [
        _row("b1", flagged=True, confirmed=True, confidence="medium", cost=0.2),
        _row("b2", flagged=False, confidence="weak", cost=0.3),
        _row("c1", label="clean", flagged=True, confidence="none", cost=0.1),
        _row("x1", status="replay_failed", include_flags=False, cost=0.4, seconds=1),
        _row("x2", status="timeout", include_flags=False, cost=0, seconds=1),
        _row("x3", status="error", include_flags=False, cost=0, seconds=1),
    ]
    directory = tmp_path / "res"
    _write_jsonl(directory, rows)
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    first.write_text(
        "case_id,hand_label,note\nb1,unrelated,old\nb2,interaction,keep\nb3,unrelated,stays\n",
        encoding="utf-8",
    )
    second.write_text(
        "case_id,hand_label,note\nb1,interaction,new\nb3,,wipe\nnope,not-a-label,ignored\n",
        encoding="utf-8",
    )
    merged = load_labels([first, second])
    assert merged["b1"] == {"hand_label": "interaction", "note": "new"}
    assert merged["b2"] == {"hand_label": "interaction", "note": "keep"}
    assert merged["b3"] == {"hand_label": "unrelated", "note": "stays"}
    assert "nope" not in merged

    out = tmp_path / "out"
    assert main(["--results", str(directory), "--labels", str(first), "--labels", str(second), "--out-dir", str(out)]) == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert (summary["n_cases"], summary["n_ok"], summary["n_replay_failed"], summary["n_timeout"], summary["n_error"]) == (
        6, 3, 1, 1, 1,
    )
    assert summary["n_flagged"] == 2
    assert summary["n_confirmed"] == 1
    assert summary["cost"]["total"] == pytest.approx(1.0)
    mined = summary["variants"]["mined"]["flagged"]["overall"]
    hand = summary["variants"]["hand"]["flagged"]["overall"]
    assert (mined["tp"], mined["fp"], mined["fn"], mined["tn"]) == (1, 1, 1, 0)
    assert (hand["tp"], hand["fp"], hand["fn"], hand["tn"]) == (1, 1, 1, 0)
    strong = summary["variants"]["mined"]["flagged"]["by_confidence"]["strong"]
    assert (strong["tp"], strong["fp"], strong["fn"], strong["tn"]) == (1, 1, 0, 0)
    text = (out / "summary.md").read_text(encoding="utf-8")
    assert text.startswith("# Bench report\n\n## Headline\n")
    assert "6 cases, 3 ok, 1 replay_failed, 1 timeout, 1 error. 2 flagged, 1 confirmed. Cost 1.000." in text
    assert "Mined flagged: TP 1, FP 1, FN 1, TN 0." in text
    assert "Hand flagged: TP 1, FP 1, FN 1, TN 0." in text
    assert "plus every clean case from those same repos." in text
    assert "| confidence strong |" in text
    with (out / "results.csv").open(newline="", encoding="utf-8") as handle:
        body = {row["case_id"]: row for row in csv.DictReader(handle)}
    assert body["b1"]["hand_label"] == "interaction"
    assert body["b1"]["note"] == "new"
    assert body["b2"]["note"] == "keep"
