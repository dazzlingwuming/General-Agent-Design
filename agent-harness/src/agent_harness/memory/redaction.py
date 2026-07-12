from __future__ import annotations

import re


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*\b", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def redact_secrets(content: str) -> tuple[str, bool]:
    """Replace common credential forms and report whether sensitive text was found."""
    redacted = content
    found = False
    for pattern in _SECRET_PATTERNS:
        redacted, count = pattern.subn("[REDACTED]", redacted)
        found = found or count > 0
    return redacted, found
