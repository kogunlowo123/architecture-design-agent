"""Secret redaction and text helpers for output that may be shared."""

from __future__ import annotations

import re

REDACTION = "[REDACTED]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END "
        r"(?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
    re.compile(
        r"(?i)(?:^|[\s\"'/-])(?:password|passwd|pwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"]?"
        r"(?!\[REDACTED\])[^\s'\",;]{4,}"
    ),
)


def redact(text: str) -> str:
    """Replace credential-shaped substrings with a placeholder."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_replace, text)
    return text


def _replace(match: re.Match[str]) -> str:
    value = match.group(0)
    key = re.search(r"(?i)(password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]", value)
    if key:
        return f"{value[: key.end()]} {REDACTION}"
    return REDACTION


def slugify(text: str) -> str:
    """Lowercase ``text`` into a filename- and identifier-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "design"


def md_cell(value: object) -> str:
    """Make ``value`` safe inside a Markdown table cell (no pipes, markup or line breaks)."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("|", "\\|").strip()


def mermaid_label(value: object) -> str:
    """Make ``value`` safe inside a quoted Mermaid label."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    for char in ('"', "<", ">", "`", "[", "]", "(", ")", "{", "}", "|", "#", ";"):
        text = text.replace(char, " ")
    return " ".join(text.split())
