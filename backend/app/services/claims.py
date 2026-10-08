"""Check the model's rationale against the exact static signature checks.

The prefilter parses both sides of each "changed by X, called by Y" pair. When
it proves that every call site still matches a changed signature
(``static_compatible``), a rationale sentence that claims a signature-type
break for that symbol (TypeError, missing/removed argument, old signature)
is unsupported.
"""
from __future__ import annotations

import re

_SIGNATURE_BREAK = re.compile(
    r"TypeError|signature|argument|parameter|keyword|missing|required|old (?:API|call)",
    re.IGNORECASE,
)
# ...and asserts that something breaks.
_BREAKS = re.compile(
    r"TypeError|ValidationError|break|fail|error|crash|raise|incompatib|mismatch|old signature",
    re.IGNORECASE,
)
_NOT_A_BREAK = re.compile(
    r"compatib|still match|matches the new|no (?:break|error|mismatch)|does(?: not|n't) break|"
    r"(?:will|would) not break|won't break|safe|unaffected|not affected|remain valid|still valid|"
    r"keeps? (?:existing )?calls valid",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?;])\s+", text) if s.strip()]


def unsupported_claims(rationale: str, static_compatible: list[str]) -> list[str]:
    """``static_compatible`` lines look like ``name: detail``. Returns the
    lines whose symbol the rationale claims is broken by a signature change."""
    out: list[str] = []
    for line in static_compatible:
        name = line.split(":", 1)[0].strip()
        if not name:
            continue
        pattern = re.compile(rf"(?<![\w.]){re.escape(name)}(?!\w)")
        for sentence in _sentences(rationale):
            if (
                pattern.search(sentence)
                and _SIGNATURE_BREAK.search(sentence)
                and _BREAKS.search(sentence)
                and not _NOT_A_BREAK.search(sentence)
            ):
                out.append(line)
                break
    return out


def repair_prompt(prompt: str, reply: str, claims: list[str]) -> str:
    facts = "\n".join(f"- {c}" for c in claims)
    return (
        f"{prompt}\n\n== Your previous answer ==\n{reply.strip()}\n\n"
        "The exact static check contradicts part of that rationale. These "
        "symbols changed signature, but every call site in the other branch "
        f"matches the new signature:\n{facts}\n"
        "Do not claim a break for them. Re-assess using only breaks supported "
        "by the evidence, and reply with the JSON object only."
    )
