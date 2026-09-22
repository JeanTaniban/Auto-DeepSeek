import io
import time
from pathlib import Path

import numpy as np

from clipboard_agent.models import ExecutionRequest, ExecutionStatus, InteractionAction, InteractionKind
from clipboard_agent.test_session import PersistentTestSession, TestSessionState as SessionState, format_test_session_result
from clipboard_agent.win32_input import ScreenFrame, ScreenPoint, ScreenRect, WindowInfo


class FakeProc:
    def __init__(self, stdout_text: str = "", stderr_text: str = ""):
        self.pid = 987654
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO(stderr_text)
        self._exit = None

    def poll(self):
        return self._exit

    def wait(self, timeout=None):
        if self._exit is None:
            self._exit = 0
        return self._exit


class FakeExecutor:
    def __init__(self, proc: FakeProc):
        self.proc = proc
        self.calls = []

    def launch_target_captured(self, request, cwd):
        self.calls.append((request, cwd))
        return self.proc


class FakeDesktop:
    def __init__(self, proc: FakeProc):
        self.proc = proc
        self.target = WindowInfo(
            hwnd=200,
            pid=proc.pid,
            title="Dev App",
            rect=ScreenRect(10, 20, 430, 340),
            client_rect=ScreenRect(20, 50, 420, 330),
        )
        self.events = []

    def window_exists(self, hwnd):
        return hwnd == self.target.hwnd and self.proc.poll() is None

    def best_window_for_pids(self, pids):
        return self.target if self.proc.pid in pids and self.proc.poll() is None else None

    def window_info(self, hwnd):
        assert hwnd == self.target.hwnd
        return self.target

    def click_window_client(self, hwnd, x, y):
        self.events.append(("click", hwnd, x, y))
        return ScreenPoint(20 + x, 50 + y)

    def activate_window(self, hwnd):
        self.events.append(("activate", hwnd))

    def type_text(self, text):
        self.events.append(("type", text))

    def press_key_chord(self, key):
        self.events.append(("key", key))

    def capture_window_client(self, hwnd):
        self.events.append(("observe", hwnd))
        image = np.zeros((12, 16, 4), dtype=np.uint8)
        image[:, :, 0] = 40
        image[:, :, 1] = 80
        image[:, :, 2] = 120
        image[:, :, 3] = 255
        return ScreenFrame(16, 12, image.tobytes())

    def client_rect_screen(self, hwnd):
        return self.target.client_rect

    def capture_signature(self, rect):
        return b"stable-frame"

    def close_window(self, hwnd):
        self.events.append(("close", hwnd))
        self.proc._exit = 0


class FakeWorkspace:
    def __init__(self):
        self.events = []

    def ensure_target_workspace(self, hwnd):
        self.events.append(("target", hwnd))
        return True

    def restore_llm_workspace(self):
        self.events.append(("llm",))
        return True


def wait_done(results, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if results:
            return results[0]
        time.sleep(0.01)
    raise AssertionError("async TestSession did not finish")


def make_session(stdout_text=""):
    proc = FakeProc(stdout_text=stdout_text)
    desktop = FakeDesktop(proc)
    executor = FakeExecutor(proc)
    workspace = FakeWorkspace()
    return PersistentTestSession(desktop, executor, workspace), proc, desktop, workspace


def test_persistent_test_session_open_actions_close_keeps_same_process():
    session, proc, desktop, workspace = make_session("boot\n")
    req = ExecutionRequest("python app.py", shell="powershell", request_id="sess-1", timeout=5)

    opened = []
    session.open_async(
        req,
        Path("."),
        (InteractionAction(InteractionKind.OBSERVE, label="startup"),),
        "window",
        window_timeout_seconds=1,
        visual_stable_seconds=0.2,
        visual_poll_ms=50,
        settle_seconds=0,
        action_delay_seconds=0,
        on_status=lambda _s: None,
        on_done=opened.append,
    )
    result = wait_done(opened)
    assert result.status == ExecutionStatus.SUCCESS
    assert result.operation == "OPENED"
    assert result.session_active is True
    assert result.llm_restored is True
    assert len(result.observations) == 1
    assert session.state == SessionState.ACTIVE_BACKGROUND
    assert proc.poll() is None

    action_results = []
    actions = (
        InteractionAction(InteractionKind.CLICK, x=30, y=40),
        InteractionAction(InteractionKind.TYPE_INPUT, text="test"),
        InteractionAction(InteractionKind.KEY, key="ENTER"),
        InteractionAction(InteractionKind.OBSERVE, label="after"),
    )
    session.actions_async(
        "act-1",
        actions,
        timeout_seconds=5,
        action_delay_seconds=0,
        settle_seconds=0,
        on_status=lambda _s: None,
        on_done=action_results.append,
    )
    action_result = wait_done(action_results)
    assert action_result.status == ExecutionStatus.SUCCESS
    assert action_result.session_active is True
    assert action_result.session_id == "sess-1"
    assert action_result.actions_completed == len(actions)
    assert proc.poll() is None
    assert ("click", 200, 30, 40) in desktop.events
    assert ("type", "test") in desktop.events
    assert ("key", "ENTER") in desktop.events
    assert session.state == SessionState.ACTIVE_BACKGROUND

    closed = []
    session.close_async(
        "close-1",
        close_timeout_seconds=0.3,
        restore_delay_seconds=0,
        on_status=lambda _s: None,
        on_done=closed.append,
    )
    close_result = wait_done(closed)
    assert close_result.status == ExecutionStatus.SUCCESS
    assert close_result.operation == "CLOSED"
    assert close_result.session_active is False
    assert close_result.llm_restored is True
    assert session.state == SessionState.CLOSED
    assert proc.poll() == 0
    assert workspace.events.count(("llm",)) >= 3


def test_stdout_checkpoint_and_screenshot_marker_are_reported_and_captured():
    session, _proc, _desktop, _workspace = make_session(
        "[[CAR_CHECKPOINT:ready-ui]]\n[[CAR_SCREENSHOT:checkpoint-view]]\n"
    )
    req = ExecutionRequest("python app.py", request_id="sess-markers", timeout=5)
    results = []
    session.open_async(
        req,
        Path("."),
        (),
        "checkpoint:ready-ui",
        window_timeout_seconds=1,
        visual_stable_seconds=0.2,
        visual_poll_ms=50,
        settle_seconds=0,
        action_delay_seconds=0,
        on_status=lambda _s: None,
        on_done=results.append,
    )
    result = wait_done(results)
    assert result.status == ExecutionStatus.SUCCESS
    assert "ready-ui" in result.checkpoints
    assert [obs.label for obs in result.observations] == ["checkpoint-view"]
    assert "CAR_CHECKPOINT" in result.stdout
    session.force_close()


def test_format_test_session_result_explains_persistence_and_visual_attachment():
    from clipboard_agent.target_session import TargetObservation

    image = np.zeros((4, 5, 3), dtype=np.uint8)
    result = format_test_session_result(
        __import__("clipboard_agent.test_session", fromlist=["TestSessionResult"]).TestSessionResult(
            request_id="a1",
            session_id="s1",
            operation="ACTIONS",
            status=ExecutionStatus.SUCCESS,
            duration=0.2,
            session_active=True,
            llm_restored=True,
            observations=[TargetObservation(1, "after", image, 5, 4)],
        )
    )
    assert "#TestSessionResult" in result
    assert "SessionActive: YES" in result
    assert "Operation: ACTIONS" in result
    assert "#VisualObservation" in result


def test_open_failure_restores_llm_and_returns_process_exit_diagnostics():
    session, proc, desktop, workspace = make_session("")
    desktop.best_window_for_pids = lambda _pids: None
    proc._exit = 3
    req = ExecutionRequest("python app.py", shell="powershell", request_id="failed-open", timeout=5)
    results = []
    session.open_async(
        req, Path("."), (), "window",
        window_timeout_seconds=0.5, visual_stable_seconds=0.2, visual_poll_ms=50,
        settle_seconds=0, action_delay_seconds=0,
        on_status=lambda _s: None, on_done=results.append,
    )
    result = wait_done(results, timeout=4.0)
    assert result.status == ExecutionStatus.ERROR
    assert result.llm_restored is True
    assert "ExitCode=3" in result.note
    assert workspace.events[-1] == ("llm",)


def _animated_signature(value: int) -> bytes:
    return bytes([value, 20, 220 - value, 255]) * 64


def test_auto_readiness_accepts_continuously_rendered_dynamic_surface():
    session, _proc, desktop, _workspace = make_session()
    frames = [_animated_signature(30), _animated_signature(180)]
    counter = {"value": 0}

    def capture_signature(_rect):
        counter["value"] += 1
        return frames[counter["value"] % 2]

    desktop.capture_signature = capture_signature
    detail = session._wait_readiness(
        desktop.target,
        "auto",
        time.monotonic() + 2.0,
        visual_stable_seconds=5.0,
        visual_poll_ms=10,
        settle_seconds=0,
    )
    assert detail == "dynamic-render"
    assert counter["value"] >= 3


def test_checkpoint_readiness_requires_content_but_not_visual_stability():
    session, _proc, desktop, _workspace = make_session()
    session._checkpoints.append("first-frame")
    frames = [_animated_signature(20), _animated_signature(190)]
    counter = {"value": 0}

    def capture_signature(_rect):
        counter["value"] += 1
        return frames[counter["value"] % 2]

    desktop.capture_signature = capture_signature
    detail = session._wait_readiness(
        desktop.target,
        "checkpoint:first-frame",
        time.monotonic() + 2.0,
        visual_stable_seconds=10.0,
        visual_poll_ms=10,
        settle_seconds=0,
    )
    assert detail == "checkpoint:first-frame+content"
    assert counter["value"] >= 2
