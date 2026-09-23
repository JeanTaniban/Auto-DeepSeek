from __future__ import annotations

import os
import platform
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import messagebox, ttk

from .app import ClipboardAgentApp, PURPLE, WARNING
from .auto_repair import (
    AUTO_REPAIR_MAX_EVENTS,
    AUTO_REPAIR_WINDOW_SECONDS,
    AutoRepairDisposition,
    classify_auto_stop,
    format_system_error_result,
)
from .managed_test_session import ManagedPersistentTestSession
from .models import AgentDirective, ExecutionRequest, ExecutionStatus
from .profile_tool_host import ProfileToolHostMixin
from .profiles import ProfileManager, ProfileRegistryError, build_default_profile_registry
from .redaction import redact_secrets
from .state_machine import AutoState, AutoTransitionError
from .test_session import TestSessionResult, TestSessionState
from .win32_input import enable_per_monitor_dpi_awareness


class ProfiledClipboardAgentApp(ProfileToolHostMixin, ClipboardAgentApp):
    """ClipboardAgentApp with profiles, managed TestSession and self-repair policy.

    Profile-tool protocol/runtime glue is isolated in ProfileToolHostMixin and
    concrete domain behavior remains inside profile packages. Auto repair self
    adapts legacy `_stop_auto(reason)` call sites into a typed recovery policy
    without weakening security or user-intervention fail-safes.
    """

    def __init__(self) -> None:
        super().__init__()
        self.test_session = ManagedPersistentTestSession(self.desktop, self.executor, self.workspace)
        self._init_profile_tool_host()
        self._auto_repair_inflight = False
        self._auto_repair_events: deque[float] = deque()

    def _ensure_profile_manager(self) -> ProfileManager:
        manager = getattr(self, "profile_manager", None)
        if manager is None:
            manager = ProfileManager(
                build_default_profile_registry(desktop=getattr(self, "desktop", None)),
                active_profile_id=getattr(self.settings, "profile_id", "generic"),
            )
            self.profile_manager = manager
            self.settings.profile_id = manager.active_profile_id
        return manager

    def _build_ui(self) -> None:
        super()._build_ui()
        manager = self._ensure_profile_manager()
        project_panel = self.project_entry.master
        profile_row = ttk.Frame(project_panel, style="Panel.TFrame")
        profile_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        profile_row.columnconfigure(1, weight=1)
        ttk.Label(profile_row, text="Profil", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.profile_var = tk.StringVar(value=manager.active_profile.metadata.display_name)
        values = tuple(meta.display_name for meta in manager.registry.metadata())
        self.profile_combo = ttk.Combobox(profile_row, textvariable=self.profile_var, values=values, state="readonly", width=30)
        self.profile_combo.grid(row=0, column=1, sticky="w")
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        self.profile_status_var = tk.StringVar(value="")
        ttk.Label(profile_row, textvariable=self.profile_status_var, style="Muted.TLabel").grid(row=0, column=2, sticky="e", padx=(12, 0))
        self._refresh_profile_status()

        repair_row = ttk.Frame(project_panel, style="Panel.TFrame")
        repair_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        repair_row.columnconfigure(1, weight=1)
        self.auto_repair_self_var = tk.BooleanVar(value=bool(getattr(self.settings, "auto_repair_self", False)))
        ttk.Checkbutton(
            repair_row,
            text="Auto repair self",
            variable=self.auto_repair_self_var,
            command=self._on_auto_repair_self_toggled,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            repair_row,
            text="Erreur système récupérable → renvoi au LLM au lieu d'un arrêt Auto (sécurité/fatal inchangés).",
            style="Muted.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

    def _restore_settings(self) -> None:
        super()._restore_settings()
        manager = self._ensure_profile_manager()
        if hasattr(self, "profile_var"):
            self.profile_var.set(manager.active_profile.metadata.display_name)
        if hasattr(self, "auto_repair_self_var"):
            self.auto_repair_self_var.set(bool(getattr(self.settings, "auto_repair_self", False)))
        self._refresh_profile_status()

    def _save_settings(self) -> None:
        manager = self._ensure_profile_manager()
        self.settings.profile_id = manager.active_profile_id
        if hasattr(self, "auto_repair_self_var"):
            self.settings.auto_repair_self = bool(self.auto_repair_self_var.get())
        super()._save_settings()

    def _on_auto_repair_self_toggled(self) -> None:
        self.settings.auto_repair_self = bool(self.auto_repair_self_var.get())
        self.store.save(self.settings)
        state = "activé" if self.settings.auto_repair_self else "désactivé"
        self._append_terminal(f"[auto-repair-self] {state}.\n", "info")

    def _profile_switch_blocked(self) -> bool:
        runner = getattr(self, "profile_tool_runner", None)
        return bool(
            self.auto_enabled
            or self.executor.running
            or self.target_runner.running
            or (runner is not None and runner.running)
            or self.test_session.state != TestSessionState.CLOSED
        )

    def _on_profile_selected(self, _event=None) -> None:
        manager = self._ensure_profile_manager()
        previous_name = manager.active_profile.metadata.display_name
        requested_name = self.profile_var.get().strip()
        if self._profile_switch_blocked():
            self.profile_var.set(previous_name)
            messagebox.showwarning(
                "Changement de profil indisponible",
                "Arrêtez Agent Auto et fermez toute commande/outil/Target App/TestSession avant de changer de profil.",
            )
            return
        try:
            manager.select_by_display_name(requested_name)
        except ProfileRegistryError as exc:
            self.profile_var.set(previous_name)
            messagebox.showerror("Profil invalide", str(exc))
            return
        self.settings.profile_id = manager.active_profile_id
        self.store.save(self.settings)
        self._refresh_profile_status(run_health=True)

    def _refresh_profile_status(self, *, run_health: bool = False) -> None:
        if not hasattr(self, "profile_status_var"):
            return
        manager = self._ensure_profile_manager()
        profile = manager.active_profile
        health = ""
        if run_health:
            try:
                report = profile.health_check(self._project_root())
                health = f" · health {report.overall.value}"
            except Exception as exc:
                health = f" · health ERROR ({exc})"
        self.profile_status_var.set(
            f"{profile.current_state().value} · v{profile.metadata.version}{health}"
        )

    def _build_prompt(self) -> str:
        root = self._project_root()
        shell = "powershell" if os.name == "nt" else "bash"
        manager = self._ensure_profile_manager()
        prompt = manager.active_profile.build_initial_prompt(
            root,
            self.goal_text.get("1.0", "end"),
            shell=shell,
            os_name=platform.system(),
        )
        if bool(getattr(self.settings, "auto_repair_self", False)):
            prompt += (
                "\n\n## Auto repair self / SYSTEM_ERROR\n"
                "Le Relay peut te renvoyer `#RelayResult` avec `Kind: SYSTEM_ERROR`. "
                "C'est une erreur système locale du Relay, pas nécessairement une erreur du projet. "
                "Lis `Code`, `AutoState` et `Description`, puis réponds avec exactement une directive `#Relay` normale "
                "pour réessayer, diagnostiquer ou choisir une autre stratégie. "
                "Ne contourne jamais une condition de sécurité, `BLOCKED`, ni une demande d'intervention utilisateur.\n"
            )
        return prompt

    # ------------------------- Auto repair self -------------------------

    def _auto_repair_local_busy(self) -> bool:
        runner = getattr(self, "profile_tool_runner", None)
        return bool(
            getattr(getattr(self, "executor", None), "running", False)
            or getattr(getattr(self, "target_runner", None), "running", False)
            or getattr(getattr(self, "test_session", None), "busy", False)
            or (runner is not None and runner.running)
        )

    def _auto_repair_attempt(self, now: float) -> int | None:
        events = getattr(self, "_auto_repair_events", None)
        if events is None:
            events = deque()
            self._auto_repair_events = events
        cutoff = now - AUTO_REPAIR_WINDOW_SECONDS
        while events and events[0] < cutoff:
            events.popleft()
        if len(events) >= AUTO_REPAIR_MAX_EVENTS:
            return None
        events.append(now)
        return len(events)

    def _stop_auto(self, reason: str, *, set_status: bool = True) -> None:
        enabled = bool(getattr(self, "auto_enabled", False))
        paused = bool(getattr(self, "auto_paused", False))
        setting_enabled = bool(getattr(getattr(self, "settings", None), "auto_repair_self", False))
        if not setting_enabled or not enabled or paused:
            super()._stop_auto(reason, set_status=set_status)
            return

        decision = classify_auto_stop(
            reason,
            state=self.auto_state,
            local_busy=self._auto_repair_local_busy(),
            recovery_inflight=bool(getattr(self, "_auto_repair_inflight", False)),
        )
        if decision.disposition != AutoRepairDisposition.RECOVERABLE:
            super()._stop_auto(reason, set_status=set_status)
            return

        self._begin_auto_self_repair(reason, code=decision.code)

    def _begin_auto_self_repair(self, reason: str, *, code: str) -> None:
        if self.user_intervention.is_set():
            self._pause_auto("Intervention utilisateur détectée pendant une tentative Auto repair self.")
            return

        attempt = self._auto_repair_attempt(time.monotonic())
        if attempt is None:
            super()._stop_auto(
                f"Auto repair self arrêté : plus de {AUTO_REPAIR_MAX_EVENTS} erreurs système en "
                f"{int(AUTO_REPAIR_WINDOW_SECONDS)} s. Dernière erreur : {reason}"
            )
            return

        previous_state = self.auto_state
        self._auto_repair_inflight = True
        self._cancel_auto_jobs()
        self.auto_visual_tracker = None
        self.auto_pending_attachment = None
        try:
            self.auto_machine.transition(AutoState.RECOVERING_SYSTEM_ERROR)
        except AutoTransitionError as exc:
            self._auto_repair_inflight = False
            super()._stop_auto(f"Auto repair self impossible : {exc}. Erreur initiale : {reason}")
            return

        event_id = f"system-{time.time_ns()}"
        description = redact_secrets(str(reason))
        text = format_system_error_result(
            event_id=event_id,
            auto_state=previous_state,
            code=code,
            description=description,
            attempt=attempt,
        )
        self._append_terminal(
            f"[auto-repair-self] {code} depuis {previous_state.value} — tentative {attempt}/{AUTO_REPAIR_MAX_EVENTS}: {description}\n",
            "stderr",
        )
        self._set_status(
            "AUTO SELF-REPAIR",
            "Erreur système récupérable : renvoi du diagnostic au LLM…",
            WARNING,
        )
        job = self._schedule_auto_action(
            "auto_delay_result_to_send_seconds",
            lambda message=text: self._auto_send_message(message),
        )
        if job is None:
            self._auto_repair_inflight = False
            super()._stop_auto(
                "Auto repair self n'a pas pu programmer le renvoi SYSTEM_ERROR ; reprise automatique abandonnée."
            )

    def _auto_accept_copied_text(self, text: str) -> None:
        if bool(getattr(self, "_auto_repair_inflight", False)):
            self._auto_repair_inflight = False
            self._append_terminal("[auto-repair-self] Réponse LLM reçue après SYSTEM_ERROR ; contrôle rendu à la boucle normale.\n", "info")
        super()._auto_accept_copied_text(text)

    # ------------------------- managed TestSession -------------------------

    def _auto_dispose_test_session(self, reason: str) -> bool:
        state = self.test_session.state
        if state == TestSessionState.CLOSED:
            return True
        if self.test_session.busy:
            self._stop_auto("Réconciliation TestSession impossible : une opération locale est encore en cours.")
            return False
        session_id = self.test_session.session_id or "<unknown>"
        self._append_terminal(
            f"[test-session] Nettoyage Auto de {session_id} ({state.value}) : {reason}\n",
            "info",
        )
        self.test_session.force_close()
        workspace = getattr(self, "workspace", None)
        if workspace is not None and workspace.binding is not None:
            if not workspace.restore_llm_workspace():
                detail = workspace.last_error or "raison Win32 inconnue"
                self._stop_auto(
                    "TestSession nettoyée, mais le workspace LLM n'a pas pu être restauré : " + detail
                )
                return False
        return True

    def _handle_execution(self, request: ExecutionRequest, cwd: Path, source_auto: bool) -> None:
        if source_auto and self.test_session.state != TestSessionState.CLOSED:
            if not self._auto_dispose_test_session("EXECUTION demandée ; la session précédente n'est plus nécessaire."):
                return
        super()._handle_execution(request, cwd, source_auto)

    def _handle_multiple(self, directive: AgentDirective, cwd: Path, source_auto: bool) -> None:
        if source_auto and self.test_session.state != TestSessionState.CLOSED:
            if not self._auto_dispose_test_session("TEMP_TEST demande une cible fraîche et temporaire."):
                return
        super()._handle_multiple(directive, cwd, source_auto)

    def _handle_open_test_session(self, directive: AgentDirective, cwd: Path, source_auto: bool) -> None:
        if source_auto and self.test_session.state != TestSessionState.CLOSED:
            if not self._auto_dispose_test_session(
                "un nouvel OPEN_TEST_SESSION remplace automatiquement la session précédente."
            ):
                return
        super()._handle_open_test_session(directive, cwd, source_auto)

    def _handle_show(self, request: ExecutionRequest, cwd: Path, source_auto: bool) -> None:
        if source_auto and self.test_session.state != TestSessionState.CLOSED:
            if not self._auto_dispose_test_session("SHOW reprend la main avec une nouvelle fenêtre utilisateur."):
                return
        super()._handle_show(request, cwd, source_auto)

    def _handle_test_actions(self, directive: AgentDirective, source_auto: bool) -> None:
        if not source_auto or self.test_session.state == TestSessionState.ACTIVE_BACKGROUND:
            super()._handle_test_actions(directive, source_auto)
            return
        previous_state = self.test_session.state
        previous_session_id = self.test_session.session_id
        if previous_state != TestSessionState.CLOSED:
            if not self._auto_dispose_test_session(
                "TEST_ACTIONS ne peut pas être appliquée à une session perdue/non exploitable."
            ):
                return
        if not self._transition_auto(AutoState.TEST_ACTING):
            return
        request_id = directive.request_id or "test-actions-no-session"
        note = (
            "Aucune TestSession active n'était disponible. "
            "Le Relay a nettoyé automatiquement l'état précédent ; "
            "ouvre une nouvelle TestSession si une fenêtre persistante est encore nécessaire."
        )
        if previous_state == TestSessionState.LOST:
            note = "La TestSession précédente était LOST et a été nettoyée automatiquement. " + note
        self._finalize_test_session_result(
            TestSessionResult(
                request_id=request_id,
                session_id=previous_session_id,
                operation="ACTIONS",
                status=ExecutionStatus.ERROR,
                duration=0.0,
                actions_total=len(directive.actions),
                actions_completed=0,
                note=note,
                session_active=False,
                llm_restored=True,
                session_state=TestSessionState.CLOSED.value,
            )
        )

    def _handle_close_test_session(self, directive: AgentDirective, source_auto: bool) -> None:
        if not source_auto or self.test_session.state != TestSessionState.CLOSED:
            super()._handle_close_test_session(directive, source_auto)
            return
        if not self._transition_auto(AutoState.TEST_CLOSING):
            return
        self._finalize_test_session_result(
            TestSessionResult(
                request_id=directive.request_id or "close-already-closed",
                session_id="",
                operation="CLOSED",
                status=ExecutionStatus.SUCCESS,
                duration=0.0,
                note="Aucune TestSession n'était ouverte ; fermeture idempotente, aucun nettoyage requis.",
                session_active=False,
                llm_restored=True,
                session_state=TestSessionState.CLOSED.value,
            )
        )

    def _finalize_test_session_result(self, result: TestSessionResult) -> None:
        interrupted = bool(
            getattr(self, "test_interrupted_by_user", False)
            or self.user_intervention.is_set()
        )
        if (
            not interrupted
            and self.auto_enabled
            and not self.auto_paused
            and result.session_state == TestSessionState.LOST.value
        ):
            self.test_session.force_close()
            workspace = getattr(self, "workspace", None)
            restored = True
            if workspace is not None and workspace.binding is not None:
                restored = workspace.restore_llm_workspace()
            result.session_state = TestSessionState.CLOSED.value
            result.session_active = False
            result.llm_restored = bool(result.llm_restored or restored)
            suffix = (
                "La session LOST a été nettoyée automatiquement par le Relay ; "
                "aucun CLOSE_TEST_SESSION de maintenance n'est requis."
            )
            result.note = f"{result.note} {suffix}".strip()
            self._append_terminal("[test-session] Session LOST auto-nettoyée -> CLOSED.\n", "info")
        super()._finalize_test_session_result(result)


def main() -> None:
    enable_per_monitor_dpi_awareness()
    app = ProfiledClipboardAgentApp()
    app.mainloop()


if __name__ == "__main__":
    main()
