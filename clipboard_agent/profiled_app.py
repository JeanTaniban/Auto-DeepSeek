from __future__ import annotations

import os
import platform
import queue
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import cv2

from .app import ClipboardAgentApp, PANEL, PURPLE, SUCCESS, WARNING, DANGER
from .managed_test_session import ManagedPersistentTestSession
from .models import AgentDirective, DirectiveKind, ExecutionRequest, ExecutionStatus
from .profile_tool_protocol import ProfileToolProtocolError, parse_profile_tool_request
from .profile_tool_runtime import ProfileToolRunner, format_tool_result
from .profiles import (
    ProfileManager,
    ProfileRegistryError,
    ProfileState,
    ToolExecutionError,
    ToolRequest,
    ToolResult,
    build_default_profile_registry,
)
from .state_machine import AutoState
from .test_session import TestSessionResult, TestSessionState
from .win32_input import enable_per_monitor_dpi_awareness


class ProfiledClipboardAgentApp(ClipboardAgentApp):
    """ClipboardAgentApp with profiles, semantic tools and managed TestSession lifecycle.

    Domain-specific logic stays inside profile modules. This class only adapts
    the generic Relay UI/Auto loop to the profile boundary.
    """

    def __init__(self) -> None:
        super().__init__()
        self.test_session = ManagedPersistentTestSession(self.desktop, self.executor, self.workspace)
        manager = self._ensure_profile_manager()
        self.profile_tool_runner = ProfileToolRunner(manager)
        self.profile_event_queue: queue.Queue[tuple[ToolRequest, ToolResult | Exception, bool]] = queue.Queue()
        self.after(80, self._drain_profile_events)

    def _ensure_profile_manager(self) -> ProfileManager:
        manager = getattr(self, "profile_manager", None)
        if manager is None:
            manager = ProfileManager(
                build_default_profile_registry(),
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

    # ------------------------- semantic profile tools -------------------------

    def _handle_agent_text(self, text: str, *, source_auto: bool) -> None:
        """Intercept profile TOOL directives; delegate every legacy/core action unchanged."""
        try:
            request = parse_profile_tool_request(text)
        except ProfileToolProtocolError as exc:
            if source_auto:
                self._stop_auto(f"Réponse TOOL invalide : {exc}")
            self._set_status("ERREUR TOOL", str(exc), DANGER)
            self._append_terminal(f"[profile-tool] {exc}\n", "stderr")
            return
        if request is None:
            super()._handle_agent_text(text, source_auto=source_auto)
            return

        runner = getattr(self, "profile_tool_runner", None)
        if (
            self.executor.running
            or self.target_runner.running
            or self.test_session.busy
            or (runner is not None and runner.running)
        ):
            if source_auto:
                self._stop_auto("Outil de profil reçu alors qu'une action locale est encore en cours.")
            return

        digest = self._hash(text)
        duplicate_hash = digest in self.processed_agent_hashes
        duplicate_id = request.request_id in self.processed_request_ids
        if duplicate_hash or duplicate_id:
            if (
                source_auto
                and self.last_result_kind == DirectiveKind.TOOL
                and self.last_result_request_id == request.request_id
                and self.last_result_text
            ):
                if not self._transition_auto(AutoState.RECOVERING_LAST_RESULT):
                    return
                attachment = self.last_result_attachment
                self._schedule_auto_action(
                    "auto_delay_result_to_send_seconds",
                    (lambda: self._auto_send_message(self.last_result_text))
                    if attachment is None
                    else (lambda: self._auto_send_message(self.last_result_text, attachment)),
                )
                return
            if source_auto:
                self._stop_auto(f"ID TOOL déjà traité : {request.request_id}.")
            return

        self.processed_agent_hashes.add(digest)
        self.processed_request_ids.add(request.request_id)
        self._handle_profile_tool(request, source_auto=source_auto)

    def _handle_profile_tool(self, request: ToolRequest, *, source_auto: bool) -> None:
        manager = self._ensure_profile_manager()
        if request.profile_id != manager.active_profile_id:
            message = (
                f"Outil refusé : profil actif {manager.active_profile_id!r}, "
                f"requête pour {request.profile_id!r}."
            )
            if source_auto:
                self._stop_auto(message)
            self._set_status("OUTIL REFUSÉ", message, DANGER)
            return

        if source_auto and self.test_session.state != TestSessionState.CLOSED:
            if not self._auto_dispose_test_session(
                "un outil métier de profil nécessite un workspace sans TestSession persistante."
            ):
                return

        try:
            root = self._project_root()
            descriptors = {item.tool_id: item for item in manager.tool_descriptors(root)}
            descriptor = descriptors.get(request.tool_id)
            if descriptor is None:
                raise ToolExecutionError(f"Outil non disponible : {request.tool_id}")
            if descriptor.provider != request.provider:
                raise ToolExecutionError(
                    f"Provider attendu {descriptor.provider!r}, reçu {request.provider!r}."
                )
        except Exception as exc:
            if source_auto:
                self._stop_auto(f"Outil de profil invalide : {exc}")
            self._set_status("OUTIL INVALIDE", str(exc), DANGER)
            return

        if source_auto:
            if not self._transition_auto(AutoState.EXECUTING):
                return
        self._append_terminal(
            f"\n[profile-tool] {request.profile_id}/{request.provider}/{request.tool_id} ID={request.request_id}\n",
            "info",
        )
        self._set_status(
            "OUTIL PROFIL",
            f"{request.tool_id} — le Relay attend le verdict métier avant de rendre la main au LLM…",
            PURPLE if source_auto else SUCCESS,
        )
        self._refresh_profile_status()
        try:
            self.profile_tool_runner.start(
                request,
                root,
                on_done=lambda outcome, req=request, auto=source_auto: self.profile_event_queue.put(
                    (req, outcome, auto)
                ),
            )
        except Exception as exc:
            if source_auto:
                self._stop_auto(f"Impossible de démarrer l'outil de profil : {exc}")
            self._set_status("ERREUR OUTIL", str(exc), DANGER)

    def _drain_profile_events(self) -> None:
        try:
            while True:
                request, outcome, source_auto = self.profile_event_queue.get_nowait()
                self._finalize_profile_tool(request, outcome, source_auto=source_auto)
        except queue.Empty:
            pass
        try:
            self.after(80, self._drain_profile_events)
        except tk.TclError:
            return

    def _profile_attachment(self, result: ToolResult):
        for raw_path in result.artifacts:
            path = Path(raw_path)
            if not path.is_file():
                continue
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is not None and image.size:
                return image
        return None

    def _finalize_profile_tool(
        self,
        request: ToolRequest,
        outcome: ToolResult | Exception,
        *,
        source_auto: bool,
    ) -> None:
        manager = self._ensure_profile_manager()
        if isinstance(outcome, Exception):
            result = ToolResult(
                request_id=request.request_id,
                profile_id=request.profile_id,
                provider=request.provider,
                tool_id=request.tool_id,
                status=ExecutionStatus.ERROR,
                profile_state=manager.active_profile.current_state(),
                errors=(str(outcome),),
                recommended_next=("TOOL",),
            )
        else:
            result = outcome

        text = format_tool_result(result, max_output_chars=self.settings.max_output_chars)
        attachment = self._profile_attachment(result)
        self.execution_count += 1
        self.session_info.configure(text=f"{self.execution_count} exécution{'s' if self.execution_count > 1 else ''}")
        self.history.insert("", 0, values=(result.request_id, f"TOOL/{result.status.value}", "—"))
        self.last_result_text = text
        self.last_result_request_id = result.request_id
        self.last_result_kind = DirectiveKind.TOOL
        self.last_result_attachment = attachment
        self._refresh_profile_status()

        if source_auto and self.auto_enabled and not self.auto_paused and not self.user_intervention.is_set():
            if not self._transition_auto(AutoState.SENDING):
                return
            self._set_status(
                "AUTO ACTIF",
                "Outil métier terminé : renvoi du verdict structuré au LLM…",
                PURPLE,
            )
            self._schedule_auto_action(
                "auto_delay_result_to_send_seconds",
                (lambda: self._auto_send_message(text))
                if attachment is None
                else (lambda: self._auto_send_message(text, attachment)),
            )
            return

        self._write_clipboard(text)
        color = SUCCESS if result.status == ExecutionStatus.SUCCESS else WARNING
        detail = "Résultat TOOL copié dans le presse-papiers."
        if attachment is not None:
            detail += " Une image est disponible pour le prochain renvoi Agent Auto."
        self._set_status("RÉSULTAT TOOL PRÊT", detail, color)

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
