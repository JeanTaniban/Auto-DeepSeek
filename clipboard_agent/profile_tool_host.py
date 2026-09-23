from __future__ import annotations

import queue
import tkinter as tk
from pathlib import Path

import cv2

from .app import DANGER, PURPLE, SUCCESS, WARNING
from .models import DirectiveKind, ExecutionStatus
from .profile_tool_protocol import ProfileToolProtocolError, parse_profile_tool_request
from .profile_tool_runtime import ProfileToolRunner, format_tool_result
from .profiles import ToolExecutionError, ToolRequest, ToolResult
from .state_machine import AutoState
from .test_session import TestSessionState


class ProfileToolHostMixin:
    """Generic Relay adapter for profile-owned semantic tools.

    The mixin deliberately knows nothing about Unity or another concrete
    profile. It owns only the Relay-side protocol/runtime bridge: parsing TOOL
    directives, duplicate recovery, asynchronous execution, structured results
    and optional image attachment forwarding.
    """

    def _init_profile_tool_host(self) -> None:
        manager = self._ensure_profile_manager()
        self.profile_tool_runner = ProfileToolRunner(manager)
        self.profile_event_queue: queue.Queue[tuple[ToolRequest, ToolResult | Exception, bool]] = queue.Queue()
        self.after(80, self._drain_profile_events)

    def _handle_agent_text(self, text: str, *, source_auto: bool) -> None:
        """Intercept profile TOOL directives; delegate every core action unchanged."""
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

        if source_auto and not self._transition_auto(AutoState.PROFILE_TOOL_RUNNING):
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

    @staticmethod
    def _profile_attachment(result: ToolResult):
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
