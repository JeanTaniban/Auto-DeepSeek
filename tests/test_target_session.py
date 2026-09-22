from pathlib import Path

import numpy as np

from clipboard_agent.models import ExecutionRequest, ExecutionStatus, InteractionAction, InteractionKind
from clipboard_agent.target_session import (
    TargetObservation,
    TargetSessionRunner,
    capture_target_observation,
    compose_observation_sheet,
    format_multiple_result,
)
from clipboard_agent.win32_input import ScreenFrame, ScreenPoint, ScreenRect, WindowInfo, WindowSnapshot


class FakeProc:
    pid = 4242


class FakeExecutor:
    def launch_target(self, request, cwd):
        self.launched = (request, cwd)
        return FakeProc()


class FakeDesktop:
    def __init__(self):
        self.hwnd = 100
        self.actions = []
        self.closed = False
        self.restored = False

    def best_window_for_pids(self, pids):
        return WindowInfo(self.hwnd, 4242, "Demo", ScreenRect(10, 10, 650, 510), ScreenRect(20, 40, 620, 480))

    def activate_window(self, hwnd):
        self.actions.append(("activate", hwnd))

    def window_info(self, hwnd):
        return self.best_window_for_pids({4242})

    def window_exists(self, hwnd):
        return not self.closed and hwnd == self.hwnd

    def click_window_client(self, hwnd, x, y):
        self.actions.append(("click", hwnd, x, y))
        return ScreenPoint(20 + x, 40 + y)

    def type_text(self, text):
        self.actions.append(("type", text))

    def press_key_chord(self, chord, *, target_hwnd=None):
        self.actions.append(("key", chord, target_hwnd))

    def capture_window_client(self, hwnd):
        self.actions.append(("observe", hwnd))
        pixels = bytes([0, 0, 255, 255] * (12 * 8))
        return ScreenFrame(12, 8, pixels)

    def close_window(self, hwnd):
        self.actions.append(("close", hwnd))
        self.closed = True

    def restore_window(self, snapshot):
        self.actions.append(("restore", snapshot.hwnd if snapshot else None))
        self.restored = True
        return True


def _browser_snapshot():
    return WindowSnapshot(9, 9, "Browser", ScreenRect(0, 0, 1200, 800), 1, ScreenRect(0, 0, 1200, 800))


def test_target_session_runs_actions_closes_and_restores(monkeypatch, tmp_path: Path):
    desktop = FakeDesktop()
    runner = TargetSessionRunner(desktop, FakeExecutor())
    monkeypatch.setattr(runner, "_process_tree_pids", lambda root, known: {root})
    monkeypatch.setattr(runner, "_terminate_known_processes", lambda pids, timeout: None)

    request = ExecutionRequest("python app.py", shell="powershell", request_id="multi-1", timeout=20)
    actions = (
        InteractionAction(InteractionKind.OBSERVE, label="initial"),
        InteractionAction(InteractionKind.CLICK, x=30, y=40),
        InteractionAction(InteractionKind.TYPE_INPUT, text="test"),
        InteractionAction(InteractionKind.KEY, key="ENTER"),
        InteractionAction(InteractionKind.WAIT, wait_ms=1),
        InteractionAction(InteractionKind.OBSERVE, label="after"),
    )
    done = []
    stages = []
    runner._worker(
        request,
        tmp_path,
        actions,
        _browser_snapshot(),
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        lambda _text: None,
        done.append,
        stages.append,
    )
    result = done[0]
    assert result.status == ExecutionStatus.SUCCESS
    assert result.actions_completed == len(actions)
    assert len(result.observations) == 2
    assert result.browser_restored is True
    assert ("click", 100, 30, 40) in desktop.actions
    assert ("type", "test") in desktop.actions
    assert ("key", "ENTER", 100) in desktop.actions
    assert ("close", 100) in desktop.actions
    assert ("restore", 9) in desktop.actions
    assert stages == ["running", "restoring"]


def test_target_session_user_cancel_leaves_target_open_and_does_not_restore(monkeypatch, tmp_path: Path):
    desktop = FakeDesktop()
    runner = TargetSessionRunner(desktop, FakeExecutor())
    monkeypatch.setattr(runner, "_process_tree_pids", lambda root, known: {root})
    monkeypatch.setattr(runner, "_terminate_known_processes", lambda pids, timeout: None)
    runner._cancel.set()
    done = []
    runner._worker(
        ExecutionRequest("app.exe", request_id="cancel-1", timeout=20),
        tmp_path,
        (InteractionAction(InteractionKind.CLICK, x=1, y=1),),
        _browser_snapshot(),
        1.0, 0.0, 0.0, 0.0, 0.0,
        lambda _text: None,
        done.append,
    )
    result = done[0]
    assert result.status == ExecutionStatus.CANCELLED
    assert result.target_left_open is True
    assert result.browser_restored is False
    assert not any(item[0] == "close" for item in desktop.actions)
    assert not any(item[0] == "restore" for item in desktop.actions)


def test_observation_sheet_and_result_text():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    observations = [TargetObservation(1, "initial", image, 200, 100)]
    sheet = compose_observation_sheet(observations)
    assert sheet is not None
    assert sheet.shape[0] > 100
    assert sheet.shape[1] == 200

    from clipboard_agent.target_session import TargetSessionResult
    result = TargetSessionResult(
        request_id="m1",
        status=ExecutionStatus.SUCCESS,
        duration=1.2,
        target_title="Demo",
        client_width=200,
        client_height=100,
        actions_total=1,
        actions_completed=1,
        observations=observations,
        browser_restored=True,
    )
    text = format_multiple_result(result, "goal")
    assert text.startswith("#RelayResult\nProtocol: 2\nKind: TEMP_TEST")
    assert "LegacyMarker: #MultipleResult" in text
    assert "RecommendedNext: EXECUTION,OPEN_TEST_SESSION,SHOW,END" in text
    assert "Observations: 1" in text
    assert "#VisualObservation" in text
    assert "#GoalReminder" in text


def test_observation_sheet_uses_one_documented_scale():
    large = np.zeros((1000, 3000, 3), dtype=np.uint8)
    observations = [TargetObservation(1, "large", large, 3000, 1000)]
    sheet = compose_observation_sheet(observations, max_width=1500, max_height=2000)
    assert sheet is not None
    # 3000 -> 1500 means a 0.5 render scale, plus the fixed header.
    assert sheet.shape[1] == 1500
    assert sheet.shape[0] == 552


def test_observe_retries_transient_black_frame_and_keeps_rendered_content():
    target = WindowInfo(100, 4242, "Demo", ScreenRect(0, 0, 80, 60), ScreenRect(0, 0, 80, 60))

    class SequenceDesktop:
        def __init__(self):
            self.calls = 0

        def capture_window_client(self, _hwnd):
            self.calls += 1
            if self.calls == 1:
                return ScreenFrame(32, 24, bytes([0, 0, 0, 255]) * (32 * 24))
            return ScreenFrame(32, 24, bytes([20, 90, 180, 255]) * (32 * 24))

    desktop = SequenceDesktop()
    observation = capture_target_observation(
        desktop,
        target,
        "game",
        1,
        retry_seconds=0.2,
        retry_poll_ms=1,
    )
    assert desktop.calls == 2
    assert observation.capture_attempts == 2
    assert observation.capture_warning == ""
    assert observation.capture_non_dark_ratio > 0.9


def test_observe_marks_persistent_black_capture_as_unreliable():
    target = WindowInfo(100, 4242, "Demo", ScreenRect(0, 0, 80, 60), ScreenRect(0, 0, 80, 60))

    class BlackDesktop:
        def capture_window_client(self, _hwnd):
            return ScreenFrame(32, 24, bytes([0, 0, 0, 255]) * (32 * 24))

    observation = capture_target_observation(
        BlackDesktop(),
        target,
        "black",
        1,
        retry_seconds=0,
    )
    assert observation.capture_attempts == 1
    assert "quasi noire" in observation.capture_warning
    assert observation.capture_non_dark_ratio == 0.0
