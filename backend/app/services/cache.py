"""Disk cache for Nemotron pair assessments.

Key = (base_sha, head_a_sha, head_b_sha, model, prompt_version). Entries live
under ``workspace_dir/nemotron-cache/`` as one JSON file per key. The value
includes the ``docs_sources`` that grounded that assessment. Files written
before the field existed still load; their source list is empty.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings

# Bump when the risk-assessment system prompt or response schema changes.
PROMPT_VERSION = "risk-v11"


@dataclass
class CachedAssessment:
    risk_score: float
    rationale: str
    risk_level: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    cached_at: float
    model: str
    prompt_version: str
    # Absent on entries written before this field. ``get`` fills [].
    docs_sources: list[dict[str, str]] = field(default_factory=list)


def _dir() -> Path:
    path = Path(settings.workspace_dir).resolve() / "nemotron-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_key(
    *,
    base_sha: str,
    head_a: str,
    head_b: str,
    model: str,
    prompt_version: str = PROMPT_VERSION,
) -> str:
    # Normalize pair order so A/B and B/A collide.
    heads = "|".join(sorted((head_a, head_b)))
    raw = f"{base_sha}|{heads}|{model}|{prompt_version}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get(key: str) -> CachedAssessment | None:
    path = _dir() / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    # Older files omit docs_sources. A bad value must not drop the assessment.
    data["docs_sources"] = _sources(data.get("docs_sources"))
    try:
        return CachedAssessment(**data)
    except TypeError:
        return None


def put(key: str, value: CachedAssessment) -> None:
    path = _dir() / f"{key}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def clear() -> int:
    """Delete every cached assessment. Returns how many files were removed."""
    removed = 0
    root = _dir()
    for path in root.glob("*.json"):
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def now() -> float:
    return time.time()


def from_payload(data: dict[str, Any], *, model: str) -> CachedAssessment:
    return CachedAssessment(
        risk_score=float(data["risk_score"]),
        rationale=str(data["rationale"]),
        risk_level=data.get("risk_level"),
        prompt_tokens=int(data.get("prompt_tokens", 0)),
        completion_tokens=int(data.get("completion_tokens", 0)),
        total_tokens=int(data.get("total_tokens", 0)),
        estimated_cost_usd=float(data.get("estimated_cost_usd", 0.0)),
        cached_at=now(),
        model=model,
        prompt_version=PROMPT_VERSION,
        docs_sources=_sources(data.get("docs_sources")),
    )


def _sources(raw: Any) -> list[dict[str, str]]:
    """Keep ``{title, url, query}`` records. Missing or invalid input is []."""
    if not isinstance(raw, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url:
            continue
        title = item.get("title")
        query = item.get("query")
        cleaned.append(
            {
                "title": title if isinstance(title, str) else "",
                "url": url,
                "query": query if isinstance(query, str) else "",
            }
        )
    return cleaned
