from __future__ import annotations

from dataclasses import dataclass

from .win32_input import DesktopAutomationUnavailable, ScreenPoint, WindowInfo, WindowSnapshot, Win32DesktopInput


def select_llm_window(z_order: list[WindowInfo], *, relay_hwnd: int, relay_pid: int) -> WindowInfo | None:
    """Choose the first usable user window immediately below Relay in Z-order.

    ``EnumWindows`` returns top-level windows in Z-order on Windows.  The relay
    window is normally first because the user just clicked "Démarrer Auto".
    Starting immediately below it makes selection deterministic even when
    several Chrome/Edge/Firefox windows exist. If Relay is not present, the
    first eligible external window is used as a safe fallback.
    """
    start = 0
    for index, info in enumerate(z_order):
        if int(info.hwnd) == int(relay_hwnd):
            start = index + 1
            break

    ordered = z_order[start:] + z_order[:start]
    for info in ordered:
        if int(info.hwnd) == int(relay_hwnd) or int(info.pid) == int(relay_pid):
            continue
        if info.minimized:
            continue
        if not info.title.strip():
            continue
        if info.rect.width < 240 or info.rect.height < 160:
            continue
        return info
    return None


@dataclass(slots=True)
class WorkspaceBinding:
    relay: WindowSnapshot
    llm: WindowSnapshot
    prompt_point: ScreenPoint
    send_point: ScreenPoint


class WindowWorkspaceManager:
    """Own deterministic LLM/Target window focus for one Agent Auto session.

    Geometry is deliberately not rearranged: Prompt/Send coordinates are
    physical pixels captured by setup, so moving/resizing the browser would
    invalidate them. Workspaces are therefore implemented with stable HWNDs,
    Z-order and verified foreground ownership.
    """

    _GEOMETRY_TOLERANCE_PX = 24

    def __init__(self, desktop: Win32DesktopInput) -> None:
        self.desktop = desktop
        self.binding: WorkspaceBinding | None = None
        self._relay_topmost: bool | None = None
        self.last_error: str | None = None

    @property
    def llm_hwnd(self) -> int | None:
        return self.binding.llm.hwnd if self.binding else None

    @property
    def relay_hwnd(self) -> int | None:
        return self.binding.relay.hwnd if self.binding else None

    def bind(
        self,
        *,
        relay_hwnd: int,
        relay_pid: int,
        prompt_point: ScreenPoint,
        send_point: ScreenPoint,
    ) -> WorkspaceBinding:
        relay_root = self.desktop.root_window(relay_hwnd)
        if not relay_root:
            raise DesktopAutomationUnavailable("Impossible d'identifier la fenêtre Clipboard Agent Relay.")
        z_order = self.desktop.enumerate_windows()
        llm_info = select_llm_window(z_order, relay_hwnd=relay_root, relay_pid=relay_pid)
        if llm_info is None:
            raise DesktopAutomationUnavailable(
                "Aucune fenêtre LLM exploitable n'a été trouvée sous Clipboard Agent Relay dans le Z-order."
            )

        prompt_owner = self.desktop.window_at_point(prompt_point)
        send_owner = self.desktop.window_at_point(send_point)
        if prompt_owner != llm_info.hwnd or send_owner != llm_info.hwnd:
            raise DesktopAutomationUnavailable(
                "La fenêtre trouvée sous le Relay ne possède pas les points Prompt/Envoyer configurés. "
                "Placez le navigateur cible juste derrière le Relay puis refaites les réglages Auto si nécessaire."
            )

        relay_snapshot = self.desktop.snapshot_window(relay_root)
        llm_snapshot = self.desktop.snapshot_window(llm_info.hwnd)
        if relay_snapshot is None or llm_snapshot is None:
            raise DesktopAutomationUnavailable("Impossible de mémoriser le workspace LLM.")
        self.binding = WorkspaceBinding(
            relay=relay_snapshot,
            llm=llm_snapshot,
            prompt_point=prompt_point,
            send_point=send_point,
        )
        self._relay_topmost = None
        self.last_error = None
        return self.binding

    def _fail(self, message: str) -> bool:
        self.last_error = message
        return False

    def _set_relay_topmost(self, enabled: bool) -> None:
        binding = self.binding
        if binding is None or self._relay_topmost is enabled:
            return
        self.desktop.set_window_topmost(binding.relay.hwnd, enabled)
        self._relay_topmost = enabled

    def _llm_geometry_is_stable(self) -> bool:
        binding = self.binding
        if binding is None:
            return False
        try:
            current = self.desktop.window_rect(binding.llm.hwnd)
        except DesktopAutomationUnavailable:
            return False
        expected = binding.llm.rect
        tolerance = self._GEOMETRY_TOLERANCE_PX
        return all(
            abs(a - b) <= tolerance
            for a, b in (
                (current.left, expected.left),
                (current.top, expected.top),
                (current.right, expected.right),
                (current.bottom, expected.bottom),
            )
        )

    def _llm_points_are_still_owned(self) -> bool:
        binding = self.binding
        if binding is None:
            return False
        try:
            return (
                self.desktop.window_at_point(binding.prompt_point) == binding.llm.hwnd
                and self.desktop.window_at_point(binding.send_point) == binding.llm.hwnd
            )
        except DesktopAutomationUnavailable:
            return False

    def ensure_llm_workspace(self) -> bool:
        self.last_error = None
        binding = self.binding
        if binding is None:
            return self._fail("Workspace LLM non lié.")
        if not self.desktop.window_exists(binding.llm.hwnd):
            return self._fail(f"Fenêtre LLM disparue (hwnd={binding.llm.hwnd}).")
        if not self.desktop.window_exists(binding.relay.hwnd):
            return self._fail(f"Fenêtre Relay disparue (hwnd={binding.relay.hwnd}).")
        # Fast no-op when already correct. If focus must be repaired, only
        # foreground/Z-order change; saved geometry is never reapplied here.
        try:
            self._set_relay_topmost(True)
            if not self.desktop.is_foreground(binding.llm.hwnd):
                self.desktop.activate_window(binding.llm.hwnd)
            if not self.desktop.is_foreground(binding.llm.hwnd):
                foreground = self.desktop.foreground_window()
                return self._fail(
                    f"Focus LLM non obtenu (attendu={binding.llm.hwnd}, foreground={foreground})."
                )
            if not self._llm_geometry_is_stable():
                current = self.desktop.window_rect(binding.llm.hwnd)
                expected = binding.llm.rect
                return self._fail(
                    "Géométrie LLM modifiée pendant Auto "
                    f"(attendu={expected}, actuel={current}). Aucune fenêtre n'a été déplacée automatiquement."
                )
            if not self._llm_points_are_still_owned():
                prompt_owner = self.desktop.window_at_point(binding.prompt_point)
                send_owner = self.desktop.window_at_point(binding.send_point)
                return self._fail(
                    "Points navigateur masqués ou déplacés "
                    f"(LLM={binding.llm.hwnd}, prompt_owner={prompt_owner}, send_owner={send_owner})."
                )
            return True
        except DesktopAutomationUnavailable as exc:
            return self._fail(str(exc))

    def ensure_target_workspace(self, target_hwnd: int) -> bool:
        self.last_error = None
        binding = self.binding
        if binding is None:
            return self._fail("Workspace LLM non lié.")
        if not self.desktop.window_exists(binding.relay.hwnd):
            return self._fail(f"Fenêtre Relay disparue (hwnd={binding.relay.hwnd}).")
        if not self.desktop.window_exists(target_hwnd):
            return self._fail(f"Fenêtre Target disparue (hwnd={target_hwnd}).")
        try:
            self._set_relay_topmost(False)
            if not self.desktop.is_foreground(target_hwnd):
                self.desktop.activate_window(target_hwnd)
            if not self.desktop.is_foreground(target_hwnd):
                foreground = self.desktop.foreground_window()
                return self._fail(
                    f"Focus Target non obtenu (attendu={target_hwnd}, foreground={foreground})."
                )
            return True
        except DesktopAutomationUnavailable as exc:
            return self._fail(str(exc))

    def restore_llm_workspace(self) -> bool:
        return self.ensure_llm_workspace()

    def release(self, *, restore: bool = True) -> None:
        binding = self.binding
        self.binding = None
        self._relay_topmost = None
        self.last_error = None
        if binding is None:
            return
        try:
            if self.desktop.window_exists(binding.relay.hwnd):
                self.desktop.set_window_topmost(binding.relay.hwnd, False)
            if not restore:
                return
            # Restore original geometry. Finish with Relay because an ordinary
            # explicit stop was initiated from this application. During a
            # physical user takeover ``restore=False`` preserves user focus.
            if self.desktop.window_exists(binding.llm.hwnd):
                self.desktop.restore_window(binding.llm)
            if self.desktop.window_exists(binding.relay.hwnd):
                self.desktop.restore_window(binding.relay)
        except DesktopAutomationUnavailable:
            pass
