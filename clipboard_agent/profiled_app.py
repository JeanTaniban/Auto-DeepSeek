from __future__ import annotations

import os
import platform
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .app import ClipboardAgentApp
from .managed_test_session import ManagedPersistentTestSession
from .models import AgentDirective, ExecutionRequest, ExecutionStatus
from .profile_tool_host import ProfileToolHostMixin
from .profiles import ProfileManager, ProfileRegistryError, build_default_profile_registry
from .state_machine import AutoState
from .test_session import TestSessionResult, TestSessionState
from .win32_input import enable_per_monitor_dpi_awareness


class ProfiledClipboardAgentApp(ProfileToolHostMixin, ClipboardAgentApp):
    """ClipboardAgentApp with profile selection and managed TestSession lifecycle.

    Profile-tool protocol/runtime glue is isolated in ProfileToolHostMixin and
    concrete domain behavior remains inside profile packages.
    """

    def __init__(self) -> None:
        super().__init__()
        self.test_session = ManagedPersistentTestSession(self.desktop, self.executor, self.workspace)
        self._init_profile_tool_host()

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

    def _restore_settings(self) -> None:
        super()._restore_settings()
        manager = self._ensure_profile_manager()
        if hasattr(self, "profile_var"):
            self.profile_var.set(manager.active_profile.metadata.display_name)
        self._refresh_profile_status()

    def _save_settings(self) -> None:
        manager = self._ensure_profile_manager()
        self.settings.profile_id = manager.active_profile_id
        super()._save_settings()

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
        return manager.active_profile.build_initial_prompt(
            root,
            self.goal_text.get("1.0", "end"),
            shell=shell,
            os_name=platform.system(),
        )

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
