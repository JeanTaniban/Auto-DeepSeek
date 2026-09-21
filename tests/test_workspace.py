from clipboard_agent.win32_input import ScreenPoint, ScreenRect, WindowInfo, WindowSnapshot
from clipboard_agent.workspace import WindowWorkspaceManager, select_llm_window


def _info(hwnd, pid, title, *, minimized=False):
    rect = ScreenRect(0, 0, 1000, 700)
    return WindowInfo(hwnd, pid, title, rect, rect, minimized)


def test_select_llm_window_uses_first_eligible_below_relay():
    windows = [
        _info(10, 100, "Clipboard Agent Relay"),
        _info(20, 200, "DeepSeek - Chrome"),
        _info(30, 300, "Other Browser"),
    ]
    selected = select_llm_window(windows, relay_hwnd=10, relay_pid=100)
    assert selected and selected.hwnd == 20


def test_select_llm_window_skips_relay_pid_minimized_and_technical_windows():
    windows = [
        _info(10, 100, "Clipboard Agent Relay"),
        _info(11, 100, "Relay helper"),
        _info(12, 400, "", minimized=False),
        _info(13, 500, "Minimized", minimized=True),
        _info(20, 200, "DeepSeek"),
    ]
    selected = select_llm_window(windows, relay_hwnd=10, relay_pid=100)
    assert selected and selected.hwnd == 20


class FakeDesktop:
    def __init__(self):
        self.windows = [_info(10, 100, "Relay"), _info(20, 200, "LLM")]
        self.foreground = 10
        self.topmost = []
        self.restored = []
        self.point_owners = {(50, 50): 20, (60, 60): 20}

    def root_window(self, hwnd): return hwnd
    def enumerate_windows(self): return list(self.windows)
    def window_at_point(self, p): return self.point_owners.get((p.x, p.y))
    def snapshot_window(self, hwnd):
        i = next(w for w in self.windows if w.hwnd == hwnd)
        return WindowSnapshot(i.hwnd, i.pid, i.title, i.rect, 1, i.rect)
    def window_exists(self, hwnd): return any(w.hwnd == hwnd for w in self.windows)
    def restore_window(self, snap):
        self.restored.append(snap.hwnd)
        self.foreground = snap.hwnd
        return True
    def set_window_topmost(self, hwnd, enabled): self.topmost.append((hwnd, enabled))
    def activate_window(self, hwnd): self.foreground = hwnd
    def is_foreground(self, hwnd): return self.foreground == hwnd


def test_workspace_bind_validates_prompt_and_send_and_restores_llm():
    d = FakeDesktop()
    manager = WindowWorkspaceManager(d)
    binding = manager.bind(
        relay_hwnd=10,
        relay_pid=100,
        prompt_point=ScreenPoint(50, 50),
        send_point=ScreenPoint(60, 60),
    )
    assert binding.llm.hwnd == 20
    assert manager.ensure_llm_workspace() is True
    assert d.foreground == 20
    assert (10, True) in d.topmost


def test_workspace_target_then_restore_llm():
    d = FakeDesktop()
    d.windows.append(_info(30, 300, "Target"))
    manager = WindowWorkspaceManager(d)
    manager.bind(relay_hwnd=10, relay_pid=100, prompt_point=ScreenPoint(50, 50), send_point=ScreenPoint(60, 60))
    assert manager.ensure_target_workspace(30)
    assert d.foreground == 30
    assert (10, False) in d.topmost
    assert manager.restore_llm_workspace()
    assert d.foreground == 20
