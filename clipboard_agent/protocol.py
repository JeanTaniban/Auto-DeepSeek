from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from .models import (
    AgentDirective,
    DirectiveKind,
    ExecutionRequest,
    ExecutionResult,
    InteractionAction,
    InteractionKind,
)


class ProtocolError(ValueError):
    pass


_CONTROL_MARKER_RE = re.compile(
    r"(?mi)^#(Execution|Show|Multiple|OpenTestSession|CloseTestSession|TestActions|End)\s*$"
)
_RELAY_LINE_RE = re.compile(r"(?i)^#Relay\s*$")
_RELAY_META_RE = re.compile(
    r"^(Protocol|Action|ID|Shell|CWD|Timeout|Launch|Ready)\s*:\s*(.*?)\s*$",
    re.IGNORECASE,
)
_RELAY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_FENCE_RE = re.compile(r"```(?P<lang>[A-Za-z0-9_+.-]*)\s*\n(?P<body>.*?)\n```", re.DOTALL)
_META_RE = re.compile(r"(?mi)^(ID|Shell|CWD|Timeout)\s*:\s*(.*?)\s*$")
_MULTI_META_RE = re.compile(r"^(ID|Shell|CWD|Timeout|Launch|Ready)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_CLOSE_META_RE = re.compile(r"^(ID)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_CLICK_RE = re.compile(r"^#Click\s+(-?\d+)\s*;\s*(-?\d+)\s*$", re.IGNORECASE)
_WAIT_RE = re.compile(r"^#Wait\s+(\d+)\s*$", re.IGNORECASE)
_OBSERVE_RE = re.compile(r"^#Observe(?:\s+(.*?))?\s*$", re.IGNORECASE)
_KEY_RE = re.compile(r"^#Key\s+(.+?)\s*$", re.IGNORECASE)
_TYPE_RE = re.compile(r"^#(?:TypeInput|Typeinout|Type)\s+(.+?)\s*$", re.IGNORECASE)
_ACTION_PREFIX_RE = re.compile(r"(?mi)^#(?:Click|Wait|Observe|Key|TypeInput|Typeinout|Type)\b")

_MAX_MULTIPLE_ACTIONS = 25
_MAX_WAIT_MS = 10_000


def _request_from_parts(
    *,
    command: str,
    metadata: dict[str, str],
    raw_text: str,
    default_shell: str,
    max_timeout: int,
    fence_language: str = "",
) -> ExecutionRequest:
    command = command.strip()
    if not command:
        raise ProtocolError("Le bloc de commande est vide.")

    shell = metadata.get("shell") or (
        fence_language
        if fence_language not in {
            "text", "execution", "exec", "show", "multiple", "testactions", "opentestsession"
        }
        else ""
    ) or default_shell
    cwd = metadata.get("cwd", ".")
    request_id = metadata.get("id") or f"cmd-{uuid.uuid4().hex[:8]}"
    timeout_raw = metadata.get("timeout", "120")
    try:
        timeout = int(timeout_raw)
    except ValueError as exc:
        raise ProtocolError("Timeout invalide : un entier en secondes est attendu.") from exc
    if timeout < 1:
        raise ProtocolError("Timeout invalide : minimum 1 seconde.")
    timeout = min(timeout, max_timeout)
    return ExecutionRequest(
        command=command,
        shell=shell.lower(),
        cwd=cwd,
        timeout=timeout,
        request_id=request_id,
        raw_text=raw_text,
    )


def _parse_copy_box_body(body: str, marker_name: str) -> tuple[dict[str, str], str]:
    marker = f"#{marker_name}"
    lines = body.splitlines()
    marker_index = next((i for i, line in enumerate(lines) if line.strip().lower() == marker.lower()), None)
    if marker_index is None:
        raise ProtocolError(f"Marqueur {marker} absent du bloc copiable.")

    metadata: dict[str, str] = {}
    command_start: int | None = None
    for idx in range(marker_index + 1, len(lines)):
        line = lines[idx]
        if not line.strip():
            if metadata:
                command_start = idx + 1
                break
            continue
        match = re.match(r"^(ID|Shell|CWD|Timeout)\s*:\s*(.*?)\s*$", line, re.IGNORECASE)
        if match:
            metadata[match.group(1).lower()] = match.group(2).strip()
            continue
        command_start = idx
        break

    if command_start is None:
        raise ProtocolError(f"Aucune commande trouvée après {marker}.")
    command = "\n".join(lines[command_start:]).strip()
    return metadata, command


def _parse_command_directive(
    text: str,
    marker_match: re.Match[str],
    marker_name: str,
    default_shell: str,
    max_timeout: int,
) -> ExecutionRequest:
    marker = f"#{marker_name}"

    for fence in _FENCE_RE.finditer(text):
        if fence.start("body") <= marker_match.start() < fence.end("body"):
            metadata, command = _parse_copy_box_body(fence.group("body"), marker_name)
            return _request_from_parts(
                command=command,
                metadata=metadata,
                raw_text=text,
                default_shell=default_shell,
                max_timeout=max_timeout,
                fence_language=fence.group("lang").lower(),
            )

    tail = text[marker_match.end():]
    fence = _FENCE_RE.search(tail)
    if fence:
        metadata_area = tail[: fence.start()]
        metadata = {k.lower(): v.strip() for k, v in _META_RE.findall(metadata_area)}
        return _request_from_parts(
            command=fence.group("body"),
            metadata=metadata,
            raw_text=text,
            default_shell=default_shell,
            max_timeout=max_timeout,
            fence_language=fence.group("lang").lower(),
        )

    # Common chat Copy buttons return the code-box body without markdown fences.
    # Accept only strict metadata + blank separator + command so ordinary prose
    # cannot become executable.
    lines = tail.splitlines()
    metadata: dict[str, str] = {}
    command_start: int | None = None
    saw_separator = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if metadata:
                saw_separator = True
                command_start = idx + 1
                break
            continue

        match = re.match(r"^(ID|Shell|CWD|Timeout)\s*:\s*(.*?)\s*$", line, re.IGNORECASE)
        if match:
            metadata[match.group(1).lower()] = match.group(2).strip()
            continue
        break

    if metadata and saw_separator and command_start is not None:
        command = "\n".join(lines[command_start:]).strip()
        if command:
            return _request_from_parts(
                command=command,
                metadata=metadata,
                raw_text=text,
                default_shell=default_shell,
                max_timeout=max_timeout,
            )

    raise ProtocolError(
        f"{marker} détecté, mais la requête n'est pas dans un format valide. "
        f"Copiez le bloc complet contenant {marker}, ses métadonnées, une ligne vide puis la commande."
    )


def _extract_control_body(text: str, marker_match: re.Match[str]) -> tuple[str, str]:
    """Return the copy-box body containing a control marker and its fence language."""
    for fence in _FENCE_RE.finditer(text):
        if fence.start("body") <= marker_match.start() < fence.end("body"):
            return fence.group("body"), fence.group("lang").lower()
    return text[marker_match.start():].strip(), ""


def _extract_relay_v2_body(text: str) -> tuple[str, str] | None:
    """Return one strict canonical #Relay body.

    V2 is intentionally strict: the copied assistant response must be either
    the bare directive itself or exactly one fenced code block containing the
    directive. A fenced #Relay surrounded by prose is rejected so the agent is
    forced into one machine-readable instruction per turn.
    """
    stripped = text.strip()
    if not stripped:
        return None

    if _RELAY_LINE_RE.fullmatch(stripped.splitlines()[0].strip()):
        return stripped, ""

    candidates: list[re.Match[str]] = []
    for fence in _FENCE_RE.finditer(stripped):
        lines = [line.strip() for line in fence.group("body").splitlines() if line.strip()]
        if lines and _RELAY_LINE_RE.fullmatch(lines[0]):
            candidates.append(fence)

    if len(candidates) > 1:
        raise ProtocolError("Plusieurs directives #Relay détectées. Une seule directive est autorisée par message.")
    if len(candidates) == 1:
        fence = candidates[0]
        if fence.start() != 0 or fence.end() != len(stripped):
            raise ProtocolError(
                "Réponse #Relay V2 invalide : le message doit contenir uniquement le bloc copiable, "
                "sans titre, explication ou texte avant/après."
            )
        return fence.group("body"), fence.group("lang").lower()

    return None


def _parse_relay_v2(
    text: str,
    body: str,
    fence_language: str,
    default_shell: str,
    max_timeout: int,
) -> AgentDirective:
    lines = body.splitlines()
    marker_index = next((i for i, line in enumerate(lines) if _RELAY_LINE_RE.fullmatch(line.strip())), None)
    if marker_index is None:
        raise ProtocolError("Marqueur #Relay absent du bloc canonique.")

    metadata: dict[str, str] = {}
    payload_start: int | None = None
    for idx in range(marker_index + 1, len(lines)):
        line = lines[idx]
        if not line.strip():
            payload_start = idx + 1
            break
        match = _RELAY_META_RE.fullmatch(line.strip())
        if not match:
            raise ProtocolError(
                "Format #Relay V2 invalide : seules les métadonnées Key: Value sont autorisées avant la ligne vide."
            )
        key = match.group(1).lower()
        if key in metadata:
            raise ProtocolError(f"Métadonnée #Relay dupliquée : {match.group(1)}")
        metadata[key] = match.group(2).strip()

    payload_lines = lines[payload_start:] if payload_start is not None else []
    payload = "\n".join(payload_lines).strip()

    if metadata.get("protocol") != "2":
        raise ProtocolError("#Relay requiert Protocol: 2.")
    action = metadata.get("action", "").strip().upper()
    if not action:
        raise ProtocolError("#Relay requiert Action:.")
    request_id = metadata.get("id", "").strip()
    if not request_id or not _RELAY_ID_RE.fullmatch(request_id):
        raise ProtocolError("#Relay requiert un ID de 1 à 80 caractères [A-Za-z0-9_.:-].")

    allowed_by_action = {
        "EXECUTION": {"protocol", "action", "id", "shell", "cwd", "timeout"},
        "OPEN_TEST_SESSION": {"protocol", "action", "id", "shell", "cwd", "timeout", "launch", "ready"},
        "TEST_ACTIONS": {"protocol", "action", "id"},
        "CLOSE_TEST_SESSION": {"protocol", "action", "id"},
        "TEMP_TEST": {"protocol", "action", "id", "shell", "cwd", "timeout", "launch"},
        "SHOW": {"protocol", "action", "id", "shell", "cwd", "timeout"},
        "END": {"protocol", "action", "id"},
    }
    allowed = allowed_by_action.get(action)
    if allowed is None:
        raise ProtocolError(
            "Action #Relay inconnue. Utilisez EXECUTION, OPEN_TEST_SESSION, TEST_ACTIONS, "
            "CLOSE_TEST_SESSION, TEMP_TEST, SHOW ou END."
        )
    unexpected = sorted(set(metadata) - allowed)
    if unexpected:
        raise ProtocolError(
            f"Métadonnée(s) interdite(s) pour Action: {action} : {', '.join(unexpected)}."
        )

    request_meta = {
        key: value
        for key, value in metadata.items()
        if key in {"id", "shell", "cwd", "timeout"}
    }

    if action in {"EXECUTION", "SHOW"}:
        if not payload:
            raise ProtocolError(f"Action: {action} requiert une commande après la ligne vide.")
        request = _request_from_parts(
            command=payload,
            metadata=request_meta,
            raw_text=text,
            default_shell=default_shell,
            max_timeout=max_timeout,
            fence_language=fence_language,
        )
        kind = DirectiveKind.EXECUTION if action == "EXECUTION" else DirectiveKind.SHOW
        return AgentDirective(kind=kind, request=request, raw_text=text, request_id=request.request_id)

    if action == "OPEN_TEST_SESSION":
        launch = metadata.get("launch", "").strip()
        if not launch:
            raise ProtocolError("Action: OPEN_TEST_SESSION requiert Launch:.")
        request = _request_from_parts(
            command=launch,
            metadata=request_meta,
            raw_text=text,
            default_shell=default_shell,
            max_timeout=max_timeout,
            fence_language=fence_language,
        )
        actions = _parse_action_lines(payload_lines, allow_empty=True) if payload_start is not None else ()
        return AgentDirective(
            kind=DirectiveKind.OPEN_TEST_SESSION,
            request=request,
            actions=actions,
            raw_text=text,
            request_id=request.request_id,
            ready=_parse_ready(metadata.get("ready", "auto")),
        )

    if action == "TEST_ACTIONS":
        actions = _parse_action_lines(payload_lines, allow_empty=False) if payload_start is not None else ()
        if not actions:
            raise ProtocolError("Action: TEST_ACTIONS requiert au moins une action après la ligne vide.")
        return AgentDirective(
            kind=DirectiveKind.TEST_ACTIONS,
            actions=actions,
            raw_text=text,
            request_id=request_id,
        )

    if action == "CLOSE_TEST_SESSION":
        if payload:
            raise ProtocolError("Action: CLOSE_TEST_SESSION n'accepte pas de payload.")
        return AgentDirective(
            kind=DirectiveKind.CLOSE_TEST_SESSION,
            raw_text=text,
            request_id=request_id,
        )

    if action == "TEMP_TEST":
        launch = metadata.get("launch", "").strip()
        if not launch:
            raise ProtocolError("Action: TEMP_TEST requiert Launch:.")
        actions = _parse_action_lines(payload_lines, allow_empty=False) if payload_start is not None else ()
        if not actions:
            raise ProtocolError("Action: TEMP_TEST requiert au moins une action.")
        request = _request_from_parts(
            command=launch,
            metadata=request_meta,
            raw_text=text,
            default_shell=default_shell,
            max_timeout=max_timeout,
            fence_language=fence_language,
        )
        return AgentDirective(
            kind=DirectiveKind.MULTIPLE,
            request=request,
            actions=actions,
            raw_text=text,
            request_id=request.request_id,
        )

    if action == "END":
        return AgentDirective(
            kind=DirectiveKind.END,
            raw_text=text,
            request_id=request_id,
            summary=payload,
        )

    raise ProtocolError(f"Action #Relay non gérée : {action}")


def _normalize_key_chord(raw: str) -> str:
    raw = raw.strip()
    # A single printable character is a valid semantic key. Preserve digits,
    # punctuation and non-ASCII characters so Win32 can translate them through
    # the active keyboard layout (e.g. "1" on AZERTY, "é" on French).
    if len(raw) == 1 and raw.isprintable() and not raw.isspace():
        if re.fullmatch(r"[A-Za-z]", raw):
            return raw.upper()
        return raw

    parts = [part.strip().upper() for part in raw.split("+") if part.strip()]
    if not parts:
        raise ProtocolError("#Key vide.")
    if len(parts) > 4:
        raise ProtocolError("#Key contient trop de touches simultanées.")

    aliases = {
        "CONTROL": "CTRL",
        "RETURN": "ENTER",
        "ESCAPE": "ESC",
        "DEL": "DELETE",
        "PAGEUP": "PGUP",
        "PAGEDOWN": "PGDN",
    }
    parts = [aliases.get(part, part) for part in parts]
    forbidden = {"WIN", "WINDOWS", "META", "LWIN", "RWIN"}
    if any(part in forbidden for part in parts):
        raise ProtocolError("#Key refuse les touches Windows/globales.")
    normalized = "+".join(parts)
    if normalized in {"ALT+TAB", "ALT+ESC", "CTRL+ESC"}:
        raise ProtocolError(f"#Key {normalized} est global à Windows et n'est pas autorisé.")

    allowed_named = {
        "CTRL", "ALT", "SHIFT", "ENTER", "ESC", "SPACE", "TAB", "BACKSPACE",
        "DELETE", "INSERT", "HOME", "END", "PGUP", "PGDN", "UP", "DOWN", "LEFT", "RIGHT",
    }
    for part in parts:
        if part in allowed_named:
            continue
        if re.fullmatch(r"F(?:[1-9]|1[0-2])", part):
            continue
        if re.fullmatch(r"[A-Z0-9]", part):
            continue
        raise ProtocolError(f"Touche #Key non supportée : {part}")
    return normalized


def _parse_type_payload(raw: str) -> str:
    value = raw.strip()
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProtocolError("#TypeInput contient une chaîne JSON invalide.") from exc
        if not isinstance(parsed, str):
            raise ProtocolError("#TypeInput attend une chaîne de caractères.")
        return parsed
    return value


def _parse_interaction_action(line: str) -> InteractionAction:
    click = _CLICK_RE.fullmatch(line)
    if click:
        x, y = int(click.group(1)), int(click.group(2))
        if x < 0 or y < 0:
            raise ProtocolError("#Click utilise des coordonnées relatives positives ou nulles.")
        return InteractionAction(InteractionKind.CLICK, x=x, y=y)

    wait = _WAIT_RE.fullmatch(line)
    if wait:
        wait_ms = int(wait.group(1))
        if wait_ms > _MAX_WAIT_MS:
            raise ProtocolError(f"#Wait est limité à {_MAX_WAIT_MS} ms par action.")
        return InteractionAction(InteractionKind.WAIT, wait_ms=wait_ms)

    observe = _OBSERVE_RE.fullmatch(line)
    if observe:
        label = (observe.group(1) or "").strip()
        if len(label) > 80:
            raise ProtocolError("Le libellé #Observe est limité à 80 caractères.")
        return InteractionAction(InteractionKind.OBSERVE, label=label)

    key = _KEY_RE.fullmatch(line)
    if key:
        return InteractionAction(InteractionKind.KEY, key=_normalize_key_chord(key.group(1)))

    typed = _TYPE_RE.fullmatch(line)
    if typed:
        text = _parse_type_payload(typed.group(1))
        if len(text) > 4000:
            raise ProtocolError("#TypeInput est limité à 4000 caractères.")
        return InteractionAction(InteractionKind.TYPE_INPUT, text=text)

    raise ProtocolError(f"Action de test inconnue ou invalide : {line}")


def _parse_action_lines(lines: list[str], *, allow_empty: bool = False) -> tuple[InteractionAction, ...]:
    action_lines = [line.strip() for line in lines if line.strip()]
    if not action_lines:
        if allow_empty:
            return ()
        raise ProtocolError("La directive requiert au moins une action.")
    if len(action_lines) > _MAX_MULTIPLE_ACTIONS:
        raise ProtocolError(f"Une séquence de test est limitée à {_MAX_MULTIPLE_ACTIONS} actions.")
    return tuple(_parse_interaction_action(line) for line in action_lines)


def _parse_ready(value: str) -> str:
    ready = (value or "auto").strip().lower()
    if ready in {"auto", "content", "window"}:
        return ready
    delay = re.fullmatch(r"delay\s*:\s*(\d+)", ready)
    if delay:
        ms = int(delay.group(1))
        if ms > 60_000:
            raise ProtocolError("Ready delay est limité à 60000 ms.")
        return f"delay:{ms}"
    checkpoint = re.fullmatch(r"checkpoint\s*:\s*([A-Za-z0-9_.:-]{1,80})", ready)
    if checkpoint:
        return f"checkpoint:{checkpoint.group(1)}"
    raise ProtocolError("Ready invalide. Utilisez auto, content, window, delay:<ms> ou checkpoint:<nom>.")


def _parse_sequence_directive(
    text: str,
    marker_match: re.Match[str],
    marker_name: str,
    default_shell: str,
    max_timeout: int,
) -> AgentDirective:
    body, fence_language = _extract_control_body(text, marker_match)
    lines = body.splitlines()
    marker = f"#{marker_name}"
    marker_index = next((i for i, line in enumerate(lines) if line.strip().lower() == marker.lower()), None)
    if marker_index is None:
        raise ProtocolError(f"Marqueur {marker} absent du bloc copiable.")

    metadata: dict[str, str] = {}
    action_start: int | None = None
    for idx in range(marker_index + 1, len(lines)):
        line = lines[idx]
        if not line.strip():
            if metadata:
                action_start = idx + 1
                break
            continue
        match = _MULTI_META_RE.fullmatch(line.strip())
        if not match:
            # For TestActions/Multiple without metadata, the first action may
            # directly follow the marker.
            if line.lstrip().startswith("#") and marker_name.lower() in {"testactions", "multiple"}:
                action_start = idx
                break
            raise ProtocolError(f"{marker} attend ses métadonnées puis une ligne vide avant les actions.")
        key = match.group(1).lower()
        if key in metadata:
            raise ProtocolError(f"Métadonnée dupliquée : {match.group(1)}")
        metadata[key] = match.group(2).strip()

    if action_start is None:
        action_start = len(lines)

    actions = _parse_action_lines(lines[action_start:], allow_empty=(marker_name.lower() == "opentestsession"))
    launch = metadata.get("launch", "").strip()

    if marker_name.lower() == "opentestsession":
        if not launch:
            raise ProtocolError("#OpenTestSession requiert `Launch: <commande>`.")
        if "\n" in launch or "\r" in launch:
            raise ProtocolError("Launch doit rester une commande atomique sur une ligne.")
        request_metadata = {k: v for k, v in metadata.items() if k not in {"launch", "ready"}}
        request = _request_from_parts(
            command=launch,
            metadata=request_metadata,
            raw_text=text,
            default_shell=default_shell,
            max_timeout=max_timeout,
            fence_language=fence_language,
        )
        return AgentDirective(
            kind=DirectiveKind.OPEN_TEST_SESSION,
            request=request,
            actions=actions,
            raw_text=text,
            request_id=request.request_id,
            ready=_parse_ready(metadata.get("ready", "auto")),
        )

    if marker_name.lower() == "multiple" and launch:
        request_metadata = {k: v for k, v in metadata.items() if k not in {"launch", "ready"}}
        request = _request_from_parts(
            command=launch,
            metadata=request_metadata,
            raw_text=text,
            default_shell=default_shell,
            max_timeout=max_timeout,
            fence_language=fence_language,
        )
        return AgentDirective(
            kind=DirectiveKind.MULTIPLE,
            request=request,
            actions=actions,
            raw_text=text,
            request_id=request.request_id,
        )

    if launch:
        raise ProtocolError(f"{marker} n'accepte pas `Launch:` dans ce contexte.")
    request_id = metadata.get("id") or f"act-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}"
    return AgentDirective(
        kind=DirectiveKind.TEST_ACTIONS,
        actions=actions,
        raw_text=text,
        request_id=request_id,
    )


def _parse_close_test_session(text: str, marker_match: re.Match[str]) -> AgentDirective:
    body, _lang = _extract_control_body(text, marker_match)
    lines = body.splitlines()
    marker_index = next((i for i, line in enumerate(lines) if line.strip().lower() == "#closetestsession"), None)
    if marker_index is None:
        raise ProtocolError("Marqueur #CloseTestSession absent.")
    metadata: dict[str, str] = {}
    for line in lines[marker_index + 1:]:
        if not line.strip():
            continue
        match = _CLOSE_META_RE.fullmatch(line.strip())
        if not match:
            raise ProtocolError("#CloseTestSession accepte uniquement `ID:` après le marqueur.")
        metadata[match.group(1).lower()] = match.group(2).strip()
    return AgentDirective(
        kind=DirectiveKind.CLOSE_TEST_SESSION,
        raw_text=text,
        request_id=metadata.get("id") or f"close-{hashlib.sha256(body.encode('utf-8')).hexdigest()[:8]}",
    )


def _parse_single_action_directive(text: str) -> AgentDirective | None:
    # A single-action shortcut must contain exactly one non-empty line after
    # optional markdown fences/prose trimming. This avoids executing an action
    # accidentally mentioned in ordinary explanatory prose.
    candidate = text.strip()
    fence = _FENCE_RE.fullmatch(candidate)
    if fence:
        candidate = fence.group("body").strip()
    lines = [line.strip() for line in candidate.splitlines() if line.strip()]
    if len(lines) != 1 or not _ACTION_PREFIX_RE.match(lines[0]):
        return None
    action = _parse_interaction_action(lines[0])
    return AgentDirective(
        kind=DirectiveKind.TEST_ACTIONS,
        actions=(action,),
        raw_text=text,
        request_id=f"act-{hashlib.sha256(candidate.encode('utf-8')).hexdigest()[:8]}",
    )


def parse_agent_directive(text: str, default_shell: str = "powershell", max_timeout: int = 1800) -> AgentDirective | None:
    """Parse exactly one agent control directive from copied chat text.

    Protocol V2 (#Relay) is canonical. Historical V1 markers remain accepted
    for backwards compatibility with already-started conversations.
    """
    text = text or ""
    relay = _extract_relay_v2_body(text)
    if relay is not None:
        body, fence_language = relay
        return _parse_relay_v2(text, body, fence_language, default_shell, max_timeout)

    matches = list(_CONTROL_MARKER_RE.finditer(text))
    if not matches:
        return _parse_single_action_directive(text)
    if len(matches) != 1:
        raise ProtocolError("Plusieurs directives de contrôle détectées. Une seule directive est autorisée par message.")

    match = matches[0]
    marker_name = match.group(1)
    marker_lower = marker_name.lower()
    if marker_lower == "end":
        summary_lines = []
        for line in text.splitlines():
            if line.strip().lower() == "#end":
                continue
            summary_lines.append(line)
        return AgentDirective(
            kind=DirectiveKind.END,
            raw_text=text,
            summary="\n".join(summary_lines).strip(),
        )

    if marker_lower in {"multiple", "testactions", "opentestsession"}:
        return _parse_sequence_directive(text, match, marker_name, default_shell, max_timeout)

    if marker_lower == "closetestsession":
        return _parse_close_test_session(text, match)

    request = _parse_command_directive(text, match, marker_name, default_shell, max_timeout)
    kind = DirectiveKind.EXECUTION if marker_lower == "execution" else DirectiveKind.SHOW
    return AgentDirective(kind=kind, request=request, raw_text=text, request_id=request.request_id)


def parse_execution(text: str, default_shell: str = "powershell", max_timeout: int = 1800) -> ExecutionRequest | None:
    """Return an execution request from canonical V2 or historical #Execution."""
    text = text or ""
    directive = parse_agent_directive(text, default_shell=default_shell, max_timeout=max_timeout)
    if directive is None:
        return None
    if directive.kind != DirectiveKind.EXECUTION or directive.request is None:
        return None
    return directive.request


def resolve_cwd(project_root: Path, requested_cwd: str) -> Path:
    root = project_root.expanduser().resolve()
    requested = Path(requested_cwd)
    target = (root / requested).resolve() if not requested.is_absolute() else requested.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ProtocolError("CWD refusé : le répertoire demandé sort de la racine du projet.") from exc
    if not target.exists():
        raise ProtocolError(f"CWD introuvable : {target}")
    if not target.is_dir():
        raise ProtocolError(f"CWD invalide : {target} n'est pas un dossier.")
    return target


def truncate_output(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    keep = max(100, (limit - 120) // 2)
    removed = len(value) - keep * 2
    return f"{value[:keep]}\n\n[OUTPUT TRUNCATED — {removed} characters removed]\n\n{value[-keep:]}"


def relay_result_header(
    kind: str,
    request_id: str,
    status: str,
    legacy_marker: str,
    recommended_next: str = "",
) -> list[str]:
    lines = [
        "#RelayResult",
        "Protocol: 2",
        f"Kind: {kind}",
        f"LegacyMarker: {legacy_marker}",
        f"ID: {request_id}",
        f"Status: {status}",
    ]
    if recommended_next:
        lines.append(f"RecommendedNext: {recommended_next}")
    return lines


def format_result(result: ExecutionResult, goal_reminder: str = "", max_output_chars: int = 100_000) -> str:
    stdout = truncate_output(result.stdout or "", max_output_chars)
    stderr = truncate_output(result.stderr or "", max_output_chars)
    lines = relay_result_header(
        "EXECUTION",
        result.request_id,
        result.status.value,
        "#ExecutionResult",
    ) + [
        f"ExitCode: {result.exit_code if result.exit_code is not None else 'N/A'}",
        f"Duration: {result.duration:.2f}s",
        f"CWD: {result.cwd}",
    ]
    if result.note:
        lines += ["", "NOTE:", result.note]
    lines += ["", "STDOUT:", stdout if stdout else "<empty>", "", "STDERR:", stderr if stderr else "<empty>"]
    if goal_reminder.strip():
        lines += ["", "#GoalReminder", goal_reminder.strip()]
    return "\n".join(lines)
