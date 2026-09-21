from __future__ import annotations

import hashlib


def normalize_clipboard_text(text: str | None) -> str:
    """Return a stable representation of clipboard text across OS/Tk round-trips.

    Windows clipboard/Tk conversions may expose CRLF even if the application
    originally wrote LF. Terminal NUL characters are also transport artefacts
    and must not make an application-owned clipboard payload look external.
    """
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\x00")


def clipboard_digest(text: str | None) -> str:
    normalized = normalize_clipboard_text(text)
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()
