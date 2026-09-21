from __future__ import annotations

import re


_PATTERNS = [
    re.compile(r"(?i)(OPENAI_API_KEY|GITHUB_TOKEN|GH_TOKEN|AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|DEEPSEEK_API_KEY)\s*[=:]\s*([^\s'\"]+)"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([A-Za-z0-9._~+/-]+=*)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"),
]


def redact_secrets(text: str) -> str:
    value = text or ""
    for i, pattern in enumerate(_PATTERNS):
        if i == 0:
            value = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
        elif i == 1:
            value = pattern.sub(lambda m: f"{m.group(1)}[REDACTED]", value)
        else:
            value = pattern.sub("[REDACTED]", value)
    return value
