import time

import numpy as np

from clipboard_agent.managed_test_session import ManagedPersistentTestSession
from clipboard_agent.win32_input import ScreenFrame, ScreenRect, WindowInfo


class _Desktop:
    def __init__(self, timeline):
        self.timeline = timeline
        self.target = WindowInfo(
            hwnd=44,
            pid=55,
            title="Target",
            rect=ScreenRect(0, 0, 400, 300),
            client_rect=ScreenRect(10, 30, 390, 290),
        )
        pixel = bytes([30, 60, 90, 255])
        self.signature = pixel * 64

    def window_exists(self, hwnd):
        return hwnd == self.target.hwnd

    def client_rect_screen(self, hwnd):
        assert hwnd == self.target.hwnd
        return self.target.client_rect

    def capture_signature(self, _rect):
        self.timeline.append("signature")
        return self.signature

    def capture_window_client(self, hwnd):
        assert hwnd == self.target.hwnd
        self.timeline.append("capture")
        image = np.zeros((8, 10, 4), dtype=np.uint8)
        image[:, :, :3] = 80
        image[:, :, 3] = 255
        return ScreenFrame(10, 8, image.tobytes())


class _Workspace:
    def __init__(self, timeline):
        self.timeline = timeline
        self.last_error = None

    def ensure_target_workspace(self, hwnd):
        self.timeline.append(f"workspace:{hwnd}")
        return True


class _Executor:
    pass


def test_readiness_reconciles_target_before_every_visible_sample():
    timeline = []
    desktop = _Desktop(timeline)
    workspace = _Workspace(timeline)
    session = ManagedPersistentTestSession(desktop, _Executor(), workspace)

    detail = session._wait_surface_ready(
        desktop.target,
        time.monotonic() + 1.0,
        stable_seconds=0.2,
        poll_ms=10,
        mode="content",
    )

    assert detail == "content"
    sample_positions = [i for i, value in enumerate(timeline) if value == "signature"]
    assert len(sample_positions) >= 2
    for position in sample_positions:
        assert position > 0
        assert timeline[position - 1] == "workspace:44"


def test_observation_reconciles_target_immediately_before_capture():
    timeline = []
    desktop = _Desktop(timeline)
    workspace = _Workspace(timeline)
    session = ManagedPersistentTestSession(desktop, _Executor(), workspace)
    observations = []

    session._capture_observation(desktop.target, "visible", observations)

    assert len(observations) == 1
    assert observations[0].label == "visible"
    assert timeline[0] == "workspace:44"
    assert "capture" in timeline
    assert timeline.index("workspace:44") < timeline.index("capture")
