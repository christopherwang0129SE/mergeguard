"""NVIDIA Nemotron client for Nebius Token Factory.

One chat-completions call per branch pair. The API key is read from settings
(``NEBIUS_API_KEY``); this module never embeds a key.

Defaults: model ``nvidia/nemotron-3-super-120b-a12b`` on the Token Factory
host ``https://api.tokenfactory.nebius.com/v1/``. Override with
``NEBIUS_MODEL`` / ``NEBIUS_BASE_URL``.

- https://nebius.com/services/token-factory/nemotron
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from openai import OpenAI

from app.config import settings
from app.schemas import BranchDiff, ChangedFile, FileCollision, PairRisk, RiskLevel

# Prompt budget. Excerpts are dropped before the structured summary is cut.
MAX_PROMPT_CHARS = 9000
MAX_EXCERPT_CHARS = 4000
MIN_EXCERPT_CHARS = 300
MAX_FILES = 12
MAX_COLLISIONS = 8
MAX_RATIONALE_CHARS = 500

_LEVELS: set[str] = {"none", "low", "medium", "high"}

SYSTEM_PROMPT = (
    "You assess whether two in-flight git branches are likely to conflict or "
    "break each other when both land on the same base branch. Judge the "
    "COMBINED behaviour after both branches land: look for semantic conflicts "
    "the diff heuristic cannot see — changed assumptions, operation order, "
    "invariants, incompatible edits to the same logic, signature changes "
    "versus call sites. The git heuristic is context only: it sees textual "
    "hunk overlap and adjacency, not meaning. The static signature checks are "
    "exact (both sides were parsed): a call site listed as not matching WILL "
    "fail; never claim a signature break (TypeError, missing or removed "
    "argument) for a symbol listed as compatible. In the rationale, name only "
    "symbols and call sites that appear in the evidence. "
    "Reply with one JSON object and no other text, using this shape: "
    '{"risk_score": <number 0-100>, "risk_level": "none"|"low"|"medium"|"high", '
    '"rationale": "<one or two sentences>"}. '
    "0 means the branches do not interfere. 100 means a merge will almost "
    "certainly conflict or invalidate the other branch's change."
)


class NemotronError(Exception):
    """The model call failed or its output could not be used.

    ``code`` is a stable fallback reason: ``model_error`` or ``unparsable``.
    """

    def __init__(
        self, code: str, detail: str = "", prompt_tokens: int = 0, completion_tokens: int = 0
    ) -> None:
        self.code = code
        self.detail = detail
        # Tokens billed for a call that still failed (e.g. reasoning hit max_tokens).
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        super().__init__(detail or code)


@dataclass(frozen=True)
class ModelAssessment:
    risk_score: float
    rationale: str
    risk_level: RiskLevel | None


def _safe_detail(exc: BaseException) -> str:
    text = str(exc)
    key = settings.nebius_api_key.strip()
    if key:
        text = text.replace(key, "[redacted]")
    text = " ".join(text.split())
    return text[:180]


class LLMText(str):
    """Model reply text that also carries token usage (0 when unknown)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __new__(cls, text: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> "LLMText":
        obj = super().__new__(cls, text)
        obj.prompt_tokens = int(prompt_tokens or 0)
        obj.completion_tokens = int(completion_tokens or 0)
        return obj


def usage_of(text: str) -> tuple[int, int]:
    """(prompt_tokens, completion_tokens) for a reply; (0, 0) when unknown."""
    return (
        int(getattr(text, "prompt_tokens", 0) or 0),
        int(getattr(text, "completion_tokens", 0) or 0),
    )


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """Listed Nemotron 3 Super price (see config) applied to token counts."""
    cost = (
        prompt_tokens * settings.nemotron_input_usd_per_1m
        + completion_tokens * settings.nemotron_output_usd_per_1m
    ) / 1_000_000
    return round(cost, 8)


def complete(
    user_prompt: str,
    *,
    system_prompt: str | None = None,
    max_tokens: int = 2048,
    timeout: float | None = None,
    extra_body: dict | None = None,
) -> str:
    """Call Token Factory chat completions once. No retries.

    Returns an :class:`LLMText` (a ``str``) carrying the token usage.
    """
    if not settings.nebius_api_key.strip():
        raise NemotronError("model_error", "NEBIUS_API_KEY is empty")

    client = OpenAI(
        api_key=settings.nebius_api_key,
        base_url=settings.nebius_base_url,
        timeout=timeout or settings.nebius_timeout_seconds,
        max_retries=0,
    )
    try:
        completion = client.chat.completions.create(
            model=settings.nebius_model,
            temperature=0,
            top_p=1,
            seed=42,
            max_tokens=max_tokens,
            **({"extra_body": extra_body} if extra_body else {}),
            messages=[
                {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
    except NemotronError:
        raise
    except Exception as exc:
        raise NemotronError("model_error", _safe_detail(exc)) from exc

    usage = getattr(completion, "usage", None)
    used = (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )
    if not completion.choices:
        raise NemotronError("model_error", "empty model response", *used)
    finish = getattr(completion.choices[0], "finish_reason", None)
    content = completion.choices[0].message.content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                text = getattr(part, "text", "")
                parts.append(str(text) if text else "")
        content = "".join(parts)
    if not isinstance(content, str) or not content.strip():
        detail = "empty model response"
        if finish:
            detail += f" (finish_reason={finish})"
        raise NemotronError("model_error", detail, *used)
    return LLMText(content, prompt_tokens=used[0], completion_tokens=used[1])


def assess_pair(prompt: str) -> ModelAssessment:
    """Score one branch pair. Raises ``NemotronError`` on any failure."""
    return parse_assessment(complete(prompt))


def parse_assessment(text: str) -> ModelAssessment:
    """Parse a model reply into a risk assessment.

    Accepts raw JSON, a fenced ```json block, or JSON after a <think> section.
    """
    data = _load_json_object(text)
    if "risk_score" not in data or "rationale" not in data:
        raise NemotronError("unparsable", "missing risk_score or rationale")

    score = _coerce_score(data["risk_score"])
    rationale = data["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise NemotronError("unparsable", "empty rationale")
    rationale = " ".join(rationale.split())
    if len(rationale) > MAX_RATIONALE_CHARS:
        rationale = rationale[: MAX_RATIONALE_CHARS - 3].rstrip() + "..."

    level_raw = data.get("risk_level")
    level: RiskLevel | None
    if isinstance(level_raw, str) and level_raw.strip().lower() in _LEVELS:
        level = level_raw.strip().lower()  # type: ignore[assignment]
    else:
        level = None
    return ModelAssessment(risk_score=score, rationale=rationale, risk_level=level)


def _coerce_score(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise NemotronError("unparsable", "risk_score is not a number")
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise NemotronError("unparsable", "risk_score is not a number") from exc
    if not math.isfinite(score):
        raise NemotronError("unparsable", "risk_score is not finite")
    return round(min(max(score, 0.0), 100.0), 1)


def _load_json_object(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    for candidate in (cleaned.strip(),):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data

    found = _first_json_object(cleaned)
    if found is None:
        raise NemotronError("unparsable", "response was not a JSON object")
    return found


def _first_json_object(text: str) -> dict | None:
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    snippet = text[start : index + 1]
                    try:
                        data = json.loads(snippet)
                    except json.JSONDecodeError:
                        break
                    if isinstance(data, dict):
                        return data
                    break
        start = text.find("{", start + 1)
    return None


def _fmt_hunks(hunks: list[tuple[int, int]]) -> str:
    if not hunks:
        return "none"
    shown = hunks[:8]
    parts = [f"{start}-{end}" if start != end else str(start) for start, end in shown]
    suffix = f" (+{len(hunks) - len(shown)} more)" if len(hunks) > len(shown) else ""
    return ", ".join(parts) + suffix


def _file_lines(files: list[ChangedFile]) -> list[str]:
    shown = files[:MAX_FILES]
    lines = [
        f"- {item.path} +{item.added_lines}/-{item.removed_lines} hunks={_fmt_hunks(item.hunks)}"
        for item in shown
    ]
    if len(files) > len(shown):
        lines.append(f"- ... {len(files) - len(shown)} more files omitted")
    return lines or ["- (no file changes)"]


def _commit_lines(commits: list[str]) -> list[str]:
    shown = commits[:5]
    return [f"- {item}" for item in shown] or ["- (none)"]


def _collision_lines(pair: PairRisk) -> list[str]:
    shown: list[FileCollision] = list(pair.collisions[:MAX_COLLISIONS])
    if not shown:
        return ["- (none)"]
    lines = [
        (
            f"- {col.path} overlapping={str(col.overlapping).lower()} "
            f"{pair.branch_a} hunks={_fmt_hunks(col.branch_a_hunks)} "
            f"{pair.branch_b} hunks={_fmt_hunks(col.branch_b_hunks)}"
        )
        for col in shown
    ]
    if len(pair.collisions) > len(shown):
        lines.append(f"- ... {len(pair.collisions) - len(shown)} more files omitted")
    return lines


def build_pair_prompt(
    *,
    base_branch: str,
    pair: PairRisk,
    diff_a: BranchDiff,
    diff_b: BranchDiff,
    commits_a: list[str],
    commits_b: list[str],
    excerpt: str,
) -> str:
    """Bounded context for one pair: files, hunks, commit subjects, diff excerpt."""
    git_score = pair.git_score if pair.git_score is not None else pair.score
    sections = [
        f"Base branch: {base_branch}",
        f"Pair: {pair.branch_a} vs {pair.branch_b}",
        (
            "Git heuristic (textual hunk overlap and adjacency only): "
            f"score={git_score} shared_files={pair.shared_files} "
            f"overlapping_files={pair.overlapping_files}"
        ),
        f"Git rationale: {pair.git_rationale or pair.rationale}",
        (
            "Static prefilter: symbols one branch changes that the other changes or calls: "
            + (", ".join(pair.symbol_overlap[:12]) if pair.symbol_overlap else "none")
        ),
        *[f"- {line}" for line in pair.symbol_evidence[:8]],
        "Static signature checks (exact):",
        *([f"- DOES NOT MATCH: {line}" for line in pair.static_breaks[:6]]
          + [f"- compatible: {line}" for line in pair.static_compatible[:6]]
          or ["- (no signature change between a changed definition and its callers)"]),
        "",
        f"Branch {pair.branch_a} (ahead {diff_a.ahead})",
        "Recent commits:",
        *_commit_lines(commits_a),
        "Changed files:",
        *_file_lines(diff_a.files),
        "",
        f"Branch {pair.branch_b} (ahead {diff_b.ahead})",
        "Recent commits:",
        *_commit_lines(commits_b),
        "Changed files:",
        *_file_lines(diff_b.files),
        "",
        "Shared or overlapping regions:",
        *_collision_lines(pair),
    ]
    body = "\n".join(sections)

    header = "\n\nDiff excerpts (trimmed):\n"
    trim_marker = "\n...(trimmed)"
    excerpt_text = excerpt.strip()
    # Fit the excerpt into what the body leaves of the prompt budget instead
    # of dropping it whole; it starts with the most specific evidence.
    budget = min(MAX_EXCERPT_CHARS, MAX_PROMPT_CHARS - len(body) - len(header) - len(trim_marker))
    if len(excerpt_text) > budget:
        excerpt_text = (
            excerpt_text[:budget].rstrip() + trim_marker if budget >= MIN_EXCERPT_CHARS else ""
        )
        if not excerpt_text:
            excerpt_text = "(omitted; context budget)"
    prompt = body + header + (excerpt_text or "(none)")
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[: MAX_PROMPT_CHARS - len(trim_marker)].rstrip() + trim_marker
    return prompt
