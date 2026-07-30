"""Sensitive-data filtering shared by durable and human-readable artifacts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import ValidationError

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:password|passwd|token|secret|api_?key|authorization|cookie)(?:$|_)",
    re.IGNORECASE,
)
_SAFE_REFERENCE_KEY = re.compile(
    r"(?:_ref|_reference|_id|_budget|_limit|_count|_usage|_percent|_expires_at)$",
    re.IGNORECASE,
)
_TEXT_PATTERNS = (
    re.compile(
        r"\b(?:sk[-_][A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_-]{12,})\b"
    ),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(
        r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})",
    ),
    re.compile(
        r"(?i)\b(password|passwd|token|secret|api[_-]?key)"
        r"(\s*[:=]\s*)([^\s,;]+)",
    ),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?"
        r"-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


class SensitiveDataRedactor:
    def __init__(self, secret_values: Sequence[str] = ()):
        self.secret_values = tuple(
            sorted(
                {value for value in secret_values if value},
                key=len,
                reverse=True,
            )
        )

    def redact_text(self, value: str) -> str:
        redacted = value
        for secret in self.secret_values:
            redacted = redacted.replace(secret, REDACTED)
        redacted = _TEXT_PATTERNS[0].sub(REDACTED, redacted)
        redacted = _TEXT_PATTERNS[1].sub(REDACTED, redacted)
        redacted = _TEXT_PATTERNS[2].sub(r"\1" + REDACTED, redacted)
        redacted = _TEXT_PATTERNS[3].sub(r"\1\2" + REDACTED, redacted)
        redacted = _TEXT_PATTERNS[4].sub(REDACTED, redacted)
        return redacted

    def redact(self, value: Any, *, key: str | None = None) -> Any:
        if key and _SENSITIVE_KEY.search(key) and not _SAFE_REFERENCE_KEY.search(key):
            return REDACTED
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            return {
                str(item_key): self.redact(item, key=str(item_key))
                for item_key, item in value.items()
            }
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        return value

    def contains_sensitive_data(self, value: Any) -> bool:
        return self.redact(value) != value

    def require_safe(self, value: Any, *, context: str) -> None:
        if self.contains_sensitive_data(value):
            raise ValidationError(
                f"{context} contains credential-like data. "
                "Use a credential reference instead of a credential value."
            )
