from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .state_machine import AutoState


AUTO_REPAIR_MAX_EVENTS = 3
AUTO_REPAIR_WINDOW_SECONDS = 120.0


class AutoRepairDisposition(str, Enum):
    NORMAL_STOP = "NORMAL_STOP"
    RECOVERABLE = "RECOVERABLE"
    FATAL = "FATAL"


@dataclass(frozen=True, slots=True)
class AutoRepairDecision:
    disposition: AutoRepairDisposition
    code: str
    rationale: str


_NORMAL_STOP_MARKERS = (
    "agent auto arrêté par l'utilisateur",
    "action end reçue",
    "show reçu : auto arrêté",
    "nouveau projet : agent auto arrêté",
)

_FATAL_MARKERS = (
    "incohérence interne agent auto",
    "commande bloquée",
    "temp_test bloqué",
    "open_test_session bloqué",
    "outil refusé : profil actif",
    "réponse reçue alors qu'une action locale est encore en cours",
    "outil de profil reçu alors qu'une action locale est encore en cours",
    "réconciliation testsession impossible : une opération locale est encore en cours",
    "workspace llm non restauré",
    "workspace llm n'a pas pu être restauré",
    "fenêtre navigateur n'a pas pu être restaurée",
    "impossible d'identifier la fenêtre navigateur",
    "erreur d'entrée win32",
    "état auto inattendu",
)

_CODE_MARKERS: tuple[tuple[str, str], ...] = (
    ("timeout visuel", "VISUAL_TIMEOUT"),
    ("bouton copier", "COPY_CHANNEL"),
    ("presse-papiers", "CLIPBOARD"),
    ("réponse agent invalide", "AGENT_PROTOCOL"),
    ("réponse tool invalide", "TOOL_PROTOCOL"),
    ("directive reçue sans commande", "DIRECTIVE_INVALID"),
    ("commande invalide", "DIRECTIVE_INVALID"),
    ("outil de profil invalide", "PROFILE_TOOL_INVALID"),
    ("impossible de démarrer l'outil de profil", "PROFILE_TOOL_START"),
    ("impossible de démarrer la commande", "EXECUTION_START"),
    ("impossible de démarrer la session target app", "TARGET_START"),
    ("impossible d'ouvrir la testsession", "TEST_SESSION_OPEN"),
    ("impossible d'exécuter les actions testsession", "TEST_SESSION_ACTIONS"),
    ("impossible de fermer la testsession", "TEST_SESSION_CLOSE"),
    ("détection visuelle copier impossible", "COPY_DETECTION"),
    ("id de directive déjà traité", "DUPLICATE_DIRECTIVE"),
    ("id tool déjà traité", "DUPLICATE_TOOL"),
)


def _normalized(reason: str) -> str:
    return " ".join(str(reason or "").strip().casefold().split())


def classify_auto_stop(
    reason: str,
    *,
    state: AutoState,
    local_busy: bool,
    recovery_inflight: bool,
) -> AutoRepairDecision:
    """Map legacy `_stop_auto(reason)` calls to explicit recovery policy.

    The historical app encoded stop semantics in human-readable reasons. Until
    all call sites carry a typed severity, this adapter keeps that dependency
    isolated and testable instead of scattering string checks across the UI.
    """

    text = _normalized(reason)
    if any(marker in text for marker in _NORMAL_STOP_MARKERS):
        return AutoRepairDecision(
            AutoRepairDisposition.NORMAL_STOP,
            "NORMAL_STOP",
            "Arrêt volontaire du cycle Auto.",
        )
    if recovery_inflight:
        return AutoRepairDecision(
            AutoRepairDisposition.FATAL,
            "RECOVERY_CHANNEL_FAILED",
            "Un nouvel incident est survenu pendant l'envoi d'un SYSTEM_ERROR.",
        )
    if local_busy:
        return AutoRepairDecision(
            AutoRepairDisposition.FATAL,
            "LOCAL_RUNTIME_BUSY",
            "Une opération locale est encore active ; rendre la main au LLM créerait une concurrence non déterministe.",
        )
    if state in {AutoState.OFF, AutoState.PAUSED, AutoState.RECOVERING_SYSTEM_ERROR}:
        return AutoRepairDecision(
            AutoRepairDisposition.FATAL,
            "AUTO_STATE_UNSAFE",
            f"L'état {state.value} ne peut pas initier une récupération système implicite.",
        )
    if any(marker in text for marker in _FATAL_MARKERS):
        return AutoRepairDecision(
            AutoRepairDisposition.FATAL,
            "FAIL_SAFE_REQUIRED",
            "L'incident touche une invariance, une politique de sécurité ou le canal LLM lui-même.",
        )

    code = "RECOVERABLE_SYSTEM_FAULT"
    for marker, candidate in _CODE_MARKERS:
        if marker in text:
            code = candidate
            break
    return AutoRepairDecision(
        AutoRepairDisposition.RECOVERABLE,
        code,
        "Incident local considéré récupérable ; le LLM peut reprendre la main avec une nouvelle directive.",
    )


def format_system_error_result(
    *,
    event_id: str,
    auto_state: AutoState,
    code: str,
    description: str,
    attempt: int,
    max_attempts: int = AUTO_REPAIR_MAX_EVENTS,
) -> str:
    clean = " ".join(str(description or "Erreur système non décrite.").strip().split())
    return "\n".join(
        [
            "#RelayResult",
            "Protocol: 2",
            "Kind: SYSTEM_ERROR",
            f"ID: {event_id}",
            "Status: ERROR",
            "Severity: RECOVERABLE",
            "Source: RELAY",
            "AutoRepairSelf: ACTIVE",
            f"AutoState: {AutoState(auto_state).value}",
            f"Code: {code}",
            f"RecoveryAttempt: {attempt}/{max_attempts}",
            f"Description: {clean}",
            (
                "Instruction: Erreur système du Relay, pas nécessairement du projet. "
                "Analyse Code/AutoState/Description puis réponds avec exactement une directive #Relay normale "
                "pour réessayer, diagnostiquer ou choisir une autre stratégie. Ne contourne jamais une condition de sécurité."
            ),
        ]
    )
