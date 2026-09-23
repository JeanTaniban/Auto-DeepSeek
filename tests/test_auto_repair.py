from __future__ import annotations

import threading
import time
from collections import deque
from types import SimpleNamespace

import pytest

from clipboard_agent.app import ClipboardAgentApp
from clipboard_agent.auto_repair import (
    AUTO_REPAIR_MAX_EVENTS,
    AutoRepairDisposition,
    classify_auto_stop,
    format_system_error_result,
)
from clipboard_agent.profiled_app import ProfiledClipboardAgentApp
from clipboard_agent.state_machine import AutoState, AutoStateMachine
from clipboard_agent.storage import Settings


def test_policy_classifies_transient_visual_fault_as_recoverable():
    decision = classify_auto_stop(
        "Timeout visuel : la zone Réponse agent n'a pas produit un cycle mouvement puis stabilité.",
        state=AutoState.WAITING_VISUAL,
        local_busy=False,
        recovery_inflight=False,
    )
    assert decision.disposition == AutoRepairDisposition.RECOVERABLE
    assert decision.code == "VISUAL_TIMEOUT"


@pytest.mark.parametrize(
    "reason",
    [
        "Incohérence interne Agent Auto : transition impossible",
        "Commande bloquée : politique locale",
        "Workspace LLM non restauré avant le clic dans le prompt.",
        "Erreur d'entrée Win32 : SendInput indisponible",
    ],
)
def test_policy_keeps_invariants_security_and_llm_channel_failures_fatal(reason):
    decision = classify_auto_stop(
        reason,
        state=AutoState.PROCESSING_REPLY,
        local_busy=False,
        recovery_inflight=False,
    )
    assert decision.disposition == AutoRepairDisposition.FATAL


def test_policy_keeps_normal_end_as_normal_stop():
    decision = classify_auto_stop(
        "Action END reçue.",
        state=AutoState.PROCESSING_REPLY,
        local_busy=False,
        recovery_inflight=False,
    )
    assert decision.disposition == AutoRepairDisposition.NORMAL_STOP


def test_policy_rejects_recovery_while_local_runtime_is_busy():
    decision = classify_auto_stop(
        "Timeout visuel",
        state=AutoState.WAITING_VISUAL,
        local_busy=True,
        recovery_inflight=False,
    )
    assert decision.disposition == AutoRepairDisposition.FATAL
    assert decision.code == "LOCAL_RUNTIME_BUSY"


def test_policy_rejects_recursive_recovery():
    decision = classify_auto_stop(
        "Le bouton Copier n'a rien copié",
        state=AutoState.SENDING,
        local_busy=False,
        recovery_inflight=True,
    )
    assert decision.disposition == AutoRepairDisposition.FATAL
    assert decision.code == "RECOVERY_CHANNEL_FAILED"


def test_system_error_result_is_explicit_and_actionable():
    text = format_system_error_result(
        event_id="system-123",
        auto_state=AutoState.WAITING_VISUAL,
        code="VISUAL_TIMEOUT",
        description="Transient visual timeout",
        attempt=2,
    )
    assert text.startswith("#RelayResult\nProtocol: 2\nKind: SYSTEM_ERROR")
    assert "LegacyMarker: #SystemError" in text
    assert "Status: ERROR" in text
    assert "Severity: RECOVERABLE" in text
    assert "Source: RELAY" in text
    assert "AutoRepairSelf: ACTIVE" in text
    assert "AutoState: WAITING_VISUAL" in text
    assert "Code: VISUAL_TIMEOUT" in text
    assert "RecoveryAttempt: 2/3" in text
    assert "directive #Relay normale" in text


def _bare_repair_app(*, state=AutoState.WAITING_VISUAL, enabled=True):
    app = object.__new__(ProfiledClipboardAgentApp)
    app.settings = Settings(auto_repair_self=enabled)
    app.auto_enabled = True
    app.auto_paused = False
    app.auto_machine = AutoStateMachine(state)
    app.user_intervention = threading.Event()
    app.executor = SimpleNamespace(running=False)
    app.target_runner = SimpleNamespace(running=False)
    app.test_session = SimpleNamespace(busy=False)
    app.profile_tool_runner = SimpleNamespace(running=False)
    app._auto_repair_inflight = False
    app._auto_repair_events = deque()
    app.auto_visual_tracker = object()
    app.auto_pending_attachment = object()
    app.logs = []
    app.statuses = []
    app.scheduled = []
    app.sent = []
    app.cancel_calls = 0
    app._cancel_auto_jobs = lambda: setattr(app, "cancel_calls", app.cancel_calls + 1)
    app._append_terminal = lambda text, tag=None: app.logs.append((text, tag))
    app._set_status = lambda *args: app.statuses.append(args)

    def schedule(setting_name, callback):
        app.scheduled.append((setting_name, callback))
        return "job-1"

    app._schedule_auto_action = schedule
    app._auto_send_message = app.sent.append
    app._pause_auto = lambda reason: setattr(app, "pause_reason", reason)
    return app


def test_recoverable_stop_becomes_system_error_without_disabling_auto(monkeypatch):
    app = _bare_repair_app()
    hard_stops = []
    monkeypatch.setattr(ClipboardAgentApp, "_stop_auto", lambda self, reason, **kwargs: hard_stops.append((reason, kwargs)))

    ProfiledClipboardAgentApp._stop_auto(
        app,
        "Timeout visuel : la réponse n'est pas devenue stable.",
    )

    assert hard_stops == []
    assert app.auto_enabled is True
    assert app.auto_machine.state == AutoState.RECOVERING_SYSTEM_ERROR
    assert app._auto_repair_inflight is True
    assert app.cancel_calls == 1
    assert app.auto_visual_tracker is None
    assert app.auto_pending_attachment is None
    assert len(app.scheduled) == 1

    app.scheduled[0][1]()
    assert len(app.sent) == 1
    assert "Kind: SYSTEM_ERROR" in app.sent[0]
    assert "Code: VISUAL_TIMEOUT" in app.sent[0]
    assert "AutoState: WAITING_VISUAL" in app.sent[0]


def test_disabled_mode_preserves_historical_hard_stop(monkeypatch):
    app = _bare_repair_app(enabled=False)
    hard_stops = []
    monkeypatch.setattr(ClipboardAgentApp, "_stop_auto", lambda self, reason, **kwargs: hard_stops.append(reason))

    ProfiledClipboardAgentApp._stop_auto(app, "Timeout visuel")

    assert hard_stops == ["Timeout visuel"]
    assert app.auto_machine.state == AutoState.WAITING_VISUAL
    assert app.scheduled == []


def test_fatal_security_fault_never_enters_self_repair(monkeypatch):
    app = _bare_repair_app(state=AutoState.PROCESSING_REPLY)
    hard_stops = []
    monkeypatch.setattr(ClipboardAgentApp, "_stop_auto", lambda self, reason, **kwargs: hard_stops.append(reason))

    ProfiledClipboardAgentApp._stop_auto(app, "Commande bloquée : opération interdite")

    assert hard_stops == ["Commande bloquée : opération interdite"]
    assert app.auto_machine.state == AutoState.PROCESSING_REPLY
    assert app.scheduled == []


def test_fault_during_recovery_fails_closed(monkeypatch):
    app = _bare_repair_app(state=AutoState.SENDING)
    app._auto_repair_inflight = True
    hard_stops = []
    monkeypatch.setattr(ClipboardAgentApp, "_stop_auto", lambda self, reason, **kwargs: hard_stops.append(reason))

    ProfiledClipboardAgentApp._stop_auto(app, "Le bouton Copier n'a produit aucun contenu")

    assert hard_stops == ["Le bouton Copier n'a produit aucun contenu"]
    assert app.scheduled == []


def test_repair_budget_fails_closed_after_three_recent_system_errors(monkeypatch):
    app = _bare_repair_app()
    now = time.monotonic()
    app._auto_repair_events = deque([now - 3, now - 2, now - 1])
    hard_stops = []
    monkeypatch.setattr(ClipboardAgentApp, "_stop_auto", lambda self, reason, **kwargs: hard_stops.append(reason))

    ProfiledClipboardAgentApp._stop_auto(app, "Timeout visuel encore présent")

    assert len(hard_stops) == 1
    assert "plus de" in hard_stops[0]
    assert str(AUTO_REPAIR_MAX_EVENTS) in hard_stops[0]
    assert app.scheduled == []


def test_new_llm_copy_releases_recovery_inflight_before_normal_processing(monkeypatch):
    app = _bare_repair_app(state=AutoState.WAITING_CLIPBOARD)
    app._auto_repair_inflight = True
    calls = []
    monkeypatch.setattr(ClipboardAgentApp, "_auto_accept_copied_text", lambda self, text: calls.append(text))

    ProfiledClipboardAgentApp._auto_accept_copied_text(app, "#Relay\nProtocol: 2")

    assert app._auto_repair_inflight is False
    assert calls == ["#Relay\nProtocol: 2"]
