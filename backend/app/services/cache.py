"""Disk cache for Nemotron pair assessments.

Key = (base_sha, head_a_sha, head_b_sha, model, prompt_version). Entries live
under ``workspace_dir/nemotron-cache/`` as one JSON file per key.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.config import settings

# Bump when the risk-assessment system prompt or response schema changes.
PROMPT_VERSION = "risk-v5"


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
    )
