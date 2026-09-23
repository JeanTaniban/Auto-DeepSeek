from __future__ import annotations

import json
import re

from .profiles.tools import ToolRequest


class ProfileToolProtocolError(ValueError):
    pass


_FENCE_RE = re.compile(r"```(?P<lang>[A-Za-z0-9_+.-]*)\s*\n(?P<body>.*?)\n```", re.DOTALL)
_RELAY_RE = re.compile(r"(?i)^#Relay\s*$")
_META_RE = re.compile(r"^(Protocol|Action|ID|Profile|Provider|Tool|Timeout)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


def _strict_body(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    lines = stripped.splitlines()
    if lines and _RELAY_RE.fullmatch(lines[0].strip()):
        return stripped
    matches = []
    for match in _FENCE_RE.finditer(stripped):
        body_lines = [line.strip() for line in match.group("body").splitlines() if line.strip()]
        if body_lines and _RELAY_RE.fullmatch(body_lines[0]):
            matches.append(match)
    if not matches:
        return None
    if len(matches) != 1 or matches[0].start() != 0 or matches[0].end() != len(stripped):
        raise ProfileToolProtocolError(
            "Une directive TOOL doit être l'unique bloc #Relay du message, sans prose avant/après."
        )
    return matches[0].group("body")


def parse_profile_tool_request(text: str, *, max_timeout: int = 600) -> ToolRequest | None:
    body = _strict_body(text)
    if body is None:
        return None
    lines = body.splitlines()
    if not lines or not _RELAY_RE.fullmatch(lines[0].strip()):
        return None

    metadata: dict[str, str] = {}
    payload_start: int | None = None
    for index in range(1, len(lines)):
        line = lines[index]
        if not line.strip():
            payload_start = index + 1
            break
        match = _META_RE.fullmatch(line.strip())
        if not match:
            # Not ours unless Action: TOOL is already known.
            if metadata.get("action", "").upper() == "TOOL":
                raise ProfileToolProtocolError(f"Métadonnée TOOL invalide : {line.strip()}")
            return None
        key = match.group(1).lower()
        if key in metadata:
            raise ProfileToolProtocolError(f"Métadonnée TOOL dupliquée : {match.group(1)}")
        metadata[key] = match.group(2).strip()

    if metadata.get("action", "").upper() != "TOOL":
        return None
    if metadata.get("protocol") != "2":
        raise ProfileToolProtocolError("Action: TOOL requiert Protocol: 2.")

    request_id = metadata.get("id", "")
    profile = metadata.get("profile", "")
    provider = metadata.get("provider", "")
    tool = metadata.get("tool", "")
    if not _ID_RE.fullmatch(request_id):
        raise ProfileToolProtocolError("Action: TOOL requiert un ID valide.")
    for label, value in (("Profile", profile), ("Provider", provider), ("Tool", tool)):
        if not _NAME_RE.fullmatch(value):
            raise ProfileToolProtocolError(f"Action: TOOL requiert {label}: avec un identifiant valide.")

    timeout: int | None = None
    if metadata.get("timeout"):
        try:
            timeout = int(metadata["timeout"])
        except ValueError as exc:
            raise ProfileToolProtocolError("Timeout TOOL invalide.") from exc
        if timeout < 1:
            raise ProfileToolProtocolError("Timeout TOOL invalide : minimum 1 seconde.")
        timeout = min(timeout, max_timeout)

    payload = "\n".join(lines[payload_start:] if payload_start is not None else []).strip()
    arguments: dict[str, object] = {}
    if payload:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProfileToolProtocolError(f"Payload TOOL JSON invalide : {exc.msg}.") from exc
        if not isinstance(parsed, dict):
            raise ProfileToolProtocolError("Payload TOOL : un objet JSON est attendu.")
        arguments = parsed

    return ToolRequest(
        request_id=request_id,
        profile_id=profile,
        provider=provider,
        tool_id=tool,
        arguments=arguments,
        timeout=timeout,
    )
