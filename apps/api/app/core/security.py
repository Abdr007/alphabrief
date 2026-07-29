"""Auth + prompt-injection containment.

Two independent concerns live here:

``require_approval_token``
    The approval / reject / edit endpoints mutate financial output, so they sit
    behind a constant-time bearer-token check (spec §7 "Security").

``harden_untrusted_text``
    News headlines and summaries are third-party text. They are *data*, never
    instructions. Everything that reaches a prompt is length-capped, stripped of
    control characters, and fenced inside an explicit untrusted-data delimiter
    with instruction-shaped phrasing neutralised.
"""

from __future__ import annotations

import hmac
import re
import unicodedata
from typing import Final

from fastapi import Header, HTTPException, status

from app.core.settings import get_settings

#: Longest single untrusted string ever placed in a prompt.
MAX_UNTRUSTED_CHARS: Final = 400

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")

#: Phrases that only make sense as an instruction to a model. Neutralised rather
#: than dropped so the analyst can still see what the source actually said.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bignore\s+(all\s+)?(previous|prior|above)\b"),
    re.compile(r"(?i)\bdisregard\s+(all\s+)?(previous|prior|above)\b"),
    re.compile(r"(?i)\byou\s+are\s+now\b"),
    re.compile(r"(?i)\bnew\s+(system\s+)?instructions?\b"),
    re.compile(r"(?i)\bsystem\s*prompt\b"),
    re.compile(r"(?i)\b(assistant|system|human)\s*:"),
    re.compile(r"(?i)</?(system|instructions?|untrusted[_-]?data)>"),
    re.compile(r"(?i)\boverride\s+(your|the)\s+\w+"),
    re.compile(r"(?i)\bre(?:-|\s)?wr\w*\s+the\s+brief\b"),
)

UNTRUSTED_OPEN: Final = "<untrusted_data>"
UNTRUSTED_CLOSE: Final = "</untrusted_data>"


def harden_untrusted_text(text: str, *, max_chars: int = MAX_UNTRUSTED_CHARS) -> str:
    """Normalise third-party text so it can never read as an instruction."""
    if not text:
        return ""
    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = _CONTROL_CHARS.sub(" ", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    for pattern in _INJECTION_PATTERNS:
        cleaned = pattern.sub("[redacted-directive]", cleaned)
    # Angle brackets can never survive: they are how our own fencing works.
    cleaned = cleaned.replace("<", "‹").replace(">", "›")
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip() + "…"
    return cleaned


def fence_untrusted(label: str, lines: list[str]) -> str:
    """Wrap hardened third-party lines in an explicit untrusted-data block."""
    body = "\n".join(f"- {harden_untrusted_text(line)}" for line in lines if line.strip())
    if not body:
        body = "- (no items)"
    return f"{UNTRUSTED_OPEN}\nsource: {label}\n{body}\n{UNTRUSTED_CLOSE}"


def verify_bearer(candidate: str | None) -> bool:
    """Constant-time comparison against the configured approval token."""
    expected = get_settings().require_approval_token()
    if not candidate:
        return False
    scheme, _, value = candidate.partition(" ")
    token = value.strip() if scheme.lower() == "bearer" and value else candidate.strip()
    return hmac.compare_digest(token, expected)


async def require_approval_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """FastAPI dependency guarding every state-changing approval endpoint."""
    if not verify_bearer(authorization):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid approval token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
