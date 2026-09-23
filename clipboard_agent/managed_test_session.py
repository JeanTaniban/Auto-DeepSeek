from __future__ import annotations

import time

from .test_session import PersistentTestSession
from .visual_watch import VisualStabilityTracker, VisualState
from .win32_input import DesktopAutomationUnavailable, WindowInfo


class ManagedPersistentTestSession(PersistentTestSession):
    """Persistent TestSession with capture-time workspace reconciliation.

    Readiness and observations can happen while the LLM workspace was restored
    between turns. Visible-screen capture is only trustworthy when the Target is
    actually above Relay at the instant pixels are sampled. This adapter keeps
    that property local to visual reads and never moves/resizes windows.
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
        # Mirror the base readiness algorithm, but reconcile Z-order immediately
        # before every visible-screen sample. Without this, a non-black Relay
        # panel covering the client rectangle can be misclassified as rendered
        # Target content.
        self._prepare_target_for_visual_read(target)
        rect = self.desktop.client_rect_screen(target.hwnd)
        baseline = self.desktop.capture_signature(rect)
        tracker = VisualStabilityTracker(
            stable_seconds=max(0.2, float(stable_seconds)),
            timeout_seconds=max(0.5, deadline - time.monotonic()),
            require_motion=False,
        )
        tracker.start(baseline, time.monotonic())
        content_streak = 1 if self._signature_has_rendered_content(target, baseline) else 0
        dynamic_streak = 0

        while time.monotonic() < deadline and not self._cancel.is_set():
            time.sleep(max(0.05, int(poll_ms) / 1000.0))
            if not self.desktop.window_exists(target.hwnd):
                return None
            self._prepare_target_for_visual_read(target)
            frame = self.desktop.capture_signature(self.desktop.client_rect_screen(target.hwnd))
            content_visible = self._signature_has_rendered_content(target, frame)
            observation = tracker.observe(frame, time.monotonic())

            if content_visible:
                content_streak += 1
                if observation.motion_ratio >= tracker.motion_threshold:
                    dynamic_streak += 1
                elif observation.state != VisualState.STABLE:
                    dynamic_streak = max(0, dynamic_streak - 1)
            else:
                content_streak = 0
                dynamic_streak = 0

            if mode == "content" and content_streak >= 2:
                return "content"
            if mode == "auto":
                if content_visible and observation.state == VisualState.STABLE:
                    return "stable"
                if content_streak >= 3 and dynamic_streak >= 2:
                    return "dynamic-render"
            if observation.state == VisualState.TIMEOUT:
                return None
        return None

    def _capture_observation(
        self,
        target: WindowInfo,
        label: str,
        observations,
        deadline: float | None = None,
    ) -> None:
        # Screenshot markers may arrive while Relay/LLM is foreground again.
        # Reconcile at the exact point of capture, then let the hardened base
        # capture retry/PrintWindow logic do its work.
        self._prepare_target_for_visual_read(target)
        super()._capture_observation(target, label, observations, deadline)


__all__ = ["ManagedPersistentTestSession"]
