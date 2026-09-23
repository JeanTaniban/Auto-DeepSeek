from __future__ import annotations

from .test_session import PersistentTestSession
from .win32_input import DesktopAutomationUnavailable, WindowInfo


class ManagedPersistentTestSession(PersistentTestSession):
    """Persistent TestSession with capture-time workspace reconciliation.

    The base implementation correctly switches to the Target workspace before
    the main action sequence. A screenshot marker can however arrive while the
    LLM workspace is active, and readiness polling reads visible screen pixels.
    In that situation a TOPMOST Relay window may cover part of the Target even
    though the Target HWND still exists. This adapter re-establishes the Target
    workspace immediately before every visual phase without moving/resizing any
    window.
    """

    def _prepare_target_for_visual_read(self, target: WindowInfo) -> None:
        if not self.workspace.ensure_target_workspace(target.hwnd):
            reason = self.workspace.last_error or "raison Win32 inconnue"
            raise DesktopAutomationUnavailable(
                "Impossible de préparer la fenêtre Target au premier plan avant capture : "
                f"{reason}"
            )

    def _wait_surface_ready(
        self,
        target: WindowInfo,
        deadline: float,
        *,
        stable_seconds: float,
        poll_ms: int,
        mode: str,
    ) -> str | None:
        # The readiness implementation samples visible screen pixels. Ensure the
        # Target owns foreground/Z-order before that sampling begins so Relay
        # pixels cannot be mistaken for application content.
        self._prepare_target_for_visual_read(target)
        return super()._wait_surface_ready(
            target,
            deadline,
            stable_seconds=stable_seconds,
            poll_ms=poll_ms,
            mode=mode,
        )

    def _capture_observation(
        self,
        target: WindowInfo,
        label: str,
        observations,
        deadline: float | None = None,
    ) -> None:
        # Screenshot markers are allowed to arrive between LLM turns, when the
        # Relay/LLM workspace is foreground again. Reconcile at the exact point
        # of capture instead of relying on an earlier focus operation.
        self._prepare_target_for_visual_read(target)
        super()._capture_observation(target, label, observations, deadline)


__all__ = ["ManagedPersistentTestSession"]
