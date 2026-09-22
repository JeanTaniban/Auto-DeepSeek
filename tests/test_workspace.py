from clipboard_agent.win32_input import ScreenPoint, ScreenRect, WindowInfo, WindowSnapshot
from clipboard_agent.workspace import WindowWorkspaceManager, select_llm_window


def _info(hwnd, pid, title, *, minimized=False, rect=None):
    rect = rect or ScreenRect(0, 0, 1000, 700)
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
        self.activations = []
        self.point_owners = {(50, 50): 20, (60, 60): 20}

    def root_window(self, hwnd): return hwnd
    def enumerate_windows(self): return list(self.windows)
    def window_at_point(self, p): return self.point_owners.get((p.x, p.y))
    def snapshot_window(self, hwnd):
        i = next(w for w in self.windows if w.hwnd == hwnd)
        return WindowSnapshot(i.hwnd, i.pid, i.title, i.rect, 1, i.rect)
    def window_exists(self, hwnd): return any(w.hwnd == hwnd for w in self.windows)
    def window_rect(self, hwnd): return next(w.rect for w in self.windows if w.hwnd == hwnd)
    def restore_window(self, snap):
        self.restored.append(snap.hwnd)
        self.foreground = snap.hwnd
        return True
    def set_window_topmost(self, hwnd, enabled): self.topmost.append((hwnd, enabled))
    def activate_window(self, hwnd):
        self.activations.append(hwnd)
        self.foreground = hwnd
    def is_foreground(self, hwnd): return self.foreground == hwnd
    def foreground_window(self): return self.foreground


def _bound_manager(d):
    manager = WindowWorkspaceManager(d)
    manager.bind(
        relay_hwnd=10,
        relay_pid=100,
        prompt_point=ScreenPoint(50, 50),
        send_point=ScreenPoint(60, 60),
    )
    return manager


def test_workspace_bind_validates_prompt_and_send_and_focuses_without_geometry_restore():
    d = FakeDesktop()
    manager = _bound_manager(d)
    assert manager.ensure_llm_workspace() is True
    assert d.foreground == 20
    assert d.activations == [20]
    assert d.restored == []
    assert d.topmost == [(10, True)]


def test_repeated_llm_workspace_check_is_a_noop_when_already_valid():
    d = FakeDesktop()
    manager = _bound_manager(d)
    assert manager.ensure_llm_workspace()
    activations = list(d.activations)
    topmost = list(d.topmost)
    assert manager.ensure_llm_workspace()
    assert d.activations == activations
    assert d.topmost == topmost
    assert d.restored == []


def test_workspace_target_then_restore_llm_changes_only_focus_and_z_order_once():
    d = FakeDesktop()
    d.windows.append(_info(30, 300, "Target"))
    manager = _bound_manager(d)
    assert manager.ensure_target_workspace(30)
    assert d.foreground == 30
    assert d.activations == [30]
    assert d.topmost == [(10, False)]
    assert manager.ensure_target_workspace(30)
    assert d.activations == [30]
    assert d.topmost == [(10, False)]
    assert manager.restore_llm_workspace()
    assert d.foreground == 20
    assert d.activations == [30, 20]
    assert d.topmost == [(10, False), (10, True)]
    assert d.restored == []
    assert manager.restore_llm_workspace()
    assert d.activations == [30, 20]
    assert d.topmost == [(10, False), (10, True)]


def test_llm_geometry_drift_fails_safe_without_repositioning_window():
    d = FakeDesktop()
    manager = _bound_manager(d)
    assert manager.ensure_llm_workspace()
    d.windows[1] = _info(20, 200, "LLM", rect=ScreenRect(100, 0, 1100, 700))
    assert manager.ensure_llm_workspace() is False
    assert d.restored == []


def test_llm_point_ownership_drift_fails_safe_without_repositioning_window():
    d = FakeDesktop()
    manager = _bound_manager(d)
    assert manager.ensure_llm_workspace()
    d.point_owners[(60, 60)] = 30
    assert manager.ensure_llm_workspace() is False
    assert d.restored == []


def test_workspace_exposes_precise_focus_failure_reason():
    d = FakeDesktop()
    manager = _bound_manager(d)
    def refuse(_hwnd):
        pass
    d.activate_window = refuse
    d.foreground = 30
    assert manager.ensure_llm_workspace() is False
    assert manager.last_error == "Focus LLM non obtenu (attendu=20, foreground=30)."
