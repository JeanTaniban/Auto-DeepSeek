from pathlib import Path

from clipboard_agent.app import ClipboardAgentApp
from clipboard_agent.models import ExecutionRequest


class _BoolVar:
    def __init__(self, value: bool):
        self.value = value

    def get(self):
        return self.value


def test_auto_execution_uses_guarded_scheduler_not_generic_tk_after(tmp_path: Path):
    class FakeApp:
        auto_enabled = True
        auto_paused = False
        auto_low_var = _BoolVar(True)
        pending_request = None
        pending_cwd = None
        pending_kind = None
        _run_pending = lambda self: None

        def _show_command(self, *args):
            self.shown = args

        def _set_status(self, *args):
            self.status = args

        def _schedule_auto(self, delay, callback):
            self.auto_scheduled = (delay, callback)

        def after(self, *_args):
            raise AssertionError("Agent Auto must not use an unguarded Tk timer to launch a command")

    req = ExecutionRequest(command="git status --short", shell="bash", request_id="safe-1")
    fake = FakeApp()
    ClipboardAgentApp._handle_execution(fake, req, tmp_path, source_auto=True)
    assert fake.auto_scheduled[0] == 100


def test_manual_low_risk_execution_keeps_generic_ui_scheduler(tmp_path: Path):
    class FakeApp:
        auto_enabled = False
        auto_paused = False
        auto_low_var = _BoolVar(True)
        pending_request = None
        pending_cwd = None
        pending_kind = None
        _run_pending = lambda self: None

        def _show_command(self, *args):
            self.shown = args

        def _set_status(self, *args):
            self.status = args

        def _schedule_auto(self, *_args):
            raise AssertionError("manual mode must not depend on Agent Auto scheduler")

        def after(self, delay, callback):
            self.manual_scheduled = (delay, callback)

    req = ExecutionRequest(command="git status --short", shell="bash", request_id="manual-1")
    fake = FakeApp()
    ClipboardAgentApp._handle_execution(fake, req, tmp_path, source_auto=False)
    assert fake.manual_scheduled[0] == 100


def test_end_from_auto_stops_before_marking_mission_complete():
    from clipboard_agent.models import AgentDirective, DirectiveKind

    events = []

    class FakeApp:
        auto_enabled = True

        def _stop_auto(self, reason, *, set_status=True):
            events.append(("stop", reason, set_status))
            self.auto_enabled = False

        def _append_terminal(self, text, tag=None):
            events.append(("terminal", text, tag))

        def _set_status(self, title, detail, color):
            events.append(("status", title, detail))

    fake = FakeApp()
    directive = AgentDirective(kind=DirectiveKind.END, summary="Tests OK")
    ClipboardAgentApp._handle_end(fake, directive, source_auto=True)
    assert events[0][0] == "stop"
    assert any(event[:2] == ("status", "AGENT TERMINÉ") for event in events)


def test_initial_attach_aborts_when_user_intervention_is_already_set():
    import threading

    class FakeDesktop:
        def capture_signature(self, _rect):
            raise AssertionError("capture must not continue after user intervention")

    class FakeApp:
        auto_enabled = True
        auto_paused = False
        user_intervention = threading.Event()
        desktop = FakeDesktop()

        def _pause_auto(self, reason):
            self.pause_reason = reason

    fake = FakeApp()
    fake.user_intervention.set()
    ClipboardAgentApp._auto_attach_existing_reply(fake, baseline=b"baseline")
    assert "Mouvement souris" in fake.pause_reason


def test_show_from_auto_stops_auto_before_scheduling_external_launch(tmp_path: Path):
    events = []

    class FakeApp:
        auto_enabled = True
        pending_request = None
        pending_cwd = None
        pending_kind = None
        _run_pending = lambda self: None

        def _stop_auto(self, reason, *, set_status=True):
            events.append(("stop", reason))
            self.auto_enabled = False

        def _show_command(self, *args):
            events.append(("show", args))

        def after(self, delay, callback):
            events.append(("schedule", delay, callback))

    req = ExecutionRequest(command="git status --short", shell="bash", request_id="show-1")
    fake = FakeApp()
    ClipboardAgentApp._handle_show(fake, req, tmp_path, source_auto=True)
    assert events[0][0] == "stop"
    assert any(event[0] == "schedule" for event in events)


def test_sensitive_auto_command_pauses_and_is_not_scheduled(tmp_path: Path):
    events = []

    class Button:
        def configure(self, **kwargs):
            events.append(("button", kwargs))

    class FakeApp:
        auto_enabled = True
        auto_paused = False
        auto_low_var = _BoolVar(True)
        run_btn = Button()
        refuse_btn = Button()
        pending_request = None
        pending_cwd = None
        pending_kind = None

        def _show_command(self, *args):
            events.append(("show", args))

        def _pause_auto(self, reason):
            events.append(("pause", reason))
            self.auto_paused = True

        def _set_status(self, *args):
            events.append(("status", args))

        def _schedule_auto(self, *_args):
            raise AssertionError("sensitive command must never be auto-scheduled")

        def after(self, *_args):
            raise AssertionError("sensitive command must never be scheduled")

    req = ExecutionRequest(command="git push origin main", shell="bash", request_id="sensitive-1")
    fake = FakeApp()
    ClipboardAgentApp._handle_execution(fake, req, tmp_path, source_auto=True)
    assert any(event[0] == "pause" for event in events)
    assert fake.auto_paused is True


def test_auto_copy_search_uses_full_virtual_screen_not_response_roi():
    import numpy as np
    from types import SimpleNamespace

    from clipboard_agent.win32_input import ScreenFrame, ScreenGeometry

    template = np.full((12, 30, 3), 50, dtype=np.uint8)
    template[3:9, 4:10] = 230
    scene = np.full((120, 220, 3), 10, dtype=np.uint8)
    y, x = 70, 150
    scene[y:y+12, x:x+30] = template
    bgra = np.concatenate(
        [scene, np.full((scene.shape[0], scene.shape[1], 1), 255, dtype=np.uint8)],
        axis=2,
    )

    class FakeDesktop:
        def capture_virtual_screen(self):
            return ScreenGeometry(-220, 40, 220, 120), ScreenFrame(220, 120, bgra.tobytes())

    class FakeApp:
        desktop = FakeDesktop()
        auto_copy_template = template
        settings = SimpleNamespace(auto_copy_match_threshold=0.86, auto_copy_template_path="unused")

        def _auto_response_rect(self):
            raise AssertionError("Copy search must not depend on the response ROI")

    point, match = ClipboardAgentApp._auto_find_copy_button(FakeApp())
    assert match.scale == 1.0
    assert match.confidence > 0.999
    assert point.x == -220 + x + 15
    assert point.y == 40 + y + 6


def _execution_text(request_id: str = "resume-1") -> str:
    return (
        "#Execution\n"
        f"ID: {request_id}\n"
        "Shell: bash\n"
        "CWD: .\n"
        "Timeout: 30\n\n"
        "printf ok"
    )


def test_initial_clipboard_accept_uses_dedicated_processing_state():
    from clipboard_agent.state_machine import AutoState, AutoStateMachine

    class FakeApp:
        auto_machine = AutoStateMachine(AutoState.WAITING_INITIAL_CLIPBOARD)

        @property
        def auto_state(self):
            return self.auto_machine.state

        def _transition_auto(self, target):
            self.auto_machine.transition(target)
            return True

        def _set_status(self, *args):
            self.status = args

        def _handle_agent_text(self, text, *, source_auto):
            self.handled = (text, source_auto, self.auto_state)

        def _stop_auto(self, reason):
            raise AssertionError(reason)

    fake = FakeApp()
    ClipboardAgentApp._auto_accept_copied_text(fake, _execution_text())
    assert fake.handled[2] == AutoState.PROCESSING_INITIAL_REPLY


def test_normal_clipboard_accept_keeps_normal_processing_state():
    from clipboard_agent.state_machine import AutoState, AutoStateMachine

    class FakeApp:
        auto_machine = AutoStateMachine(AutoState.WAITING_CLIPBOARD)

        @property
        def auto_state(self):
            return self.auto_machine.state

        def _transition_auto(self, target):
            self.auto_machine.transition(target)
            return True

        def _set_status(self, *args):
            self.status = args

        def _handle_agent_text(self, text, *, source_auto):
            self.handled = (text, source_auto, self.auto_state)

        def _stop_auto(self, reason):
            raise AssertionError(reason)

    fake = FakeApp()
    ClipboardAgentApp._auto_accept_copied_text(fake, _execution_text())
    assert fake.handled[2] == AutoState.PROCESSING_REPLY


def test_initial_duplicate_execution_reuses_matching_last_result_without_reexecution():
    from clipboard_agent.state_machine import AutoState, AutoStateMachine

    text = _execution_text("resume-42")
    digest = ClipboardAgentApp._hash(text)

    class Executor:
        running = False

    class FakeApp:
        executor = Executor()
        auto_machine = AutoStateMachine(AutoState.PROCESSING_INITIAL_REPLY)
        processed_agent_hashes = {digest}
        processed_request_ids = {"resume-42"}
        last_result_request_id = "resume-42"
        last_result_text = "#ExecutionResult\nProtocol: 1\nID: resume-42\nStatus: SUCCESS"

        @property
        def auto_state(self):
            return self.auto_machine.state

        def _hash(self, value):
            return ClipboardAgentApp._hash(value)

        def _transition_auto(self, target):
            self.auto_machine.transition(target)
            return True

        def _auto_action_delay_ms(self, name):
            assert name == "auto_delay_result_to_send_seconds"
            return 275

        def _schedule_auto(self, delay, callback):
            self.scheduled = (delay, callback)

        def _set_status(self, *args):
            self.status = args

        def _append_terminal(self, *args):
            self.log = args

        def _auto_send_message(self, value):
            self.sent = value

        def _auto_resume_from_last_result(self, directive):
            return ClipboardAgentApp._auto_resume_from_last_result(self, directive)

        def _stop_auto(self, reason):
            raise AssertionError(f"Auto must resume instead of stopping: {reason}")

        def _handle_execution(self, *_args, **_kwargs):
            raise AssertionError("The already executed command must never run again")

    fake = FakeApp()
    ClipboardAgentApp._handle_agent_text(fake, text, source_auto=True)
    assert fake.auto_state == AutoState.RECOVERING_LAST_RESULT
    assert fake.scheduled[0] == 275
    fake.scheduled[1]()
    assert fake.sent == fake.last_result_text


def test_duplicate_during_normal_cycle_still_stops_auto():
    from clipboard_agent.state_machine import AutoState

    text = _execution_text("normal-dup")
    digest = ClipboardAgentApp._hash(text)

    class Executor:
        running = False

    class FakeApp:
        executor = Executor()
        auto_state = AutoState.PROCESSING_REPLY
        processed_agent_hashes = {digest}
        processed_request_ids = {"normal-dup"}
        last_result_request_id = "normal-dup"
        last_result_text = "#ExecutionResult\nID: normal-dup"

        def _hash(self, value):
            return ClipboardAgentApp._hash(value)

        def _stop_auto(self, reason):
            self.stop_reason = reason

        def _set_status(self, *args):
            self.status = args

        def _append_terminal(self, *args):
            self.log = args

    fake = FakeApp()
    ClipboardAgentApp._handle_agent_text(fake, text, source_auto=True)
    assert "déjà traité" in fake.stop_reason


def test_initial_duplicate_with_mismatched_result_id_fails_closed():
    from clipboard_agent.state_machine import AutoState

    text = _execution_text("expected-7")
    digest = ClipboardAgentApp._hash(text)

    class Executor:
        running = False

    class FakeApp:
        executor = Executor()
        auto_state = AutoState.PROCESSING_INITIAL_REPLY
        processed_agent_hashes = {digest}
        processed_request_ids = {"expected-7"}
        last_result_request_id = "other-9"
        last_result_text = "#ExecutionResult\nID: other-9"

        def _hash(self, value):
            return ClipboardAgentApp._hash(value)

        def _auto_resume_from_last_result(self, directive):
            return ClipboardAgentApp._auto_resume_from_last_result(self, directive)

        def _stop_auto(self, reason):
            self.stop_reason = reason

        def _set_status(self, *args):
            self.status = args

        def _append_terminal(self, *args):
            self.log = args

    fake = FakeApp()
    ClipboardAgentApp._handle_agent_text(fake, text, source_auto=True)
    assert "aucun résultat local correspondant" in fake.stop_reason


def test_finalize_result_remembers_request_id_for_future_auto_resume(tmp_path: Path):
    from types import SimpleNamespace
    from clipboard_agent.models import ExecutionResult, ExecutionStatus

    class Widget:
        def configure(self, **kwargs):
            pass

    class History:
        def insert(self, *args, **kwargs):
            pass

    class Goal:
        def get(self, *_args):
            return "goal"

    class FakeApp:
        stop_btn = Widget()
        session_info = Widget()
        history = History()
        goal_text = Goal()
        settings = SimpleNamespace(goal_reminder_every=4, max_output_chars=100000)
        pending_request = object()
        pending_cwd = tmp_path
        pending_kind = None
        execution_count = 0
        last_result_text = ""
        last_result_request_id = ""
        auto_enabled = False
        auto_paused = False
        user_intervention = SimpleNamespace(is_set=lambda: False)

        def _write_clipboard(self, text):
            self.clipboard = text

        def _set_status(self, *args):
            self.status = args

    result = ExecutionResult(
        request_id="remember-123",
        status=ExecutionStatus.SUCCESS,
        exit_code=0,
        duration=0.1,
        cwd=tmp_path,
        stdout="ok",
        stderr="",
        command="printf ok",
    )
    fake = FakeApp()
    ClipboardAgentApp._finalize_result(fake, result)
    assert fake.last_result_request_id == "remember-123"
    assert "ID: remember-123" in fake.last_result_text


def test_copy_after_initial_sync_enters_waiting_initial_clipboard():
    from types import SimpleNamespace
    from clipboard_agent.state_machine import AutoState, AutoStateMachine
    from clipboard_agent.win32_input import ScreenPoint

    class Desktop:
        def clipboard_sequence_number(self):
            return 12

        def click(self, point):
            self.clicked = point

    class FakeApp:
        auto_enabled = True
        auto_paused = False
        user_intervention = SimpleNamespace(is_set=lambda: False)
        auto_machine = AutoStateMachine(AutoState.SYNCING_EXISTING_REPLY)
        desktop = Desktop()
        settings = SimpleNamespace(auto_clipboard_timeout_seconds=1.5)

        @property
        def auto_state(self):
            return self.auto_machine.state

        def _auto_find_copy_button(self):
            match = SimpleNamespace(confidence=0.99)
            return ScreenPoint(100, 200), match

        def _read_clipboard(self):
            return "old"

        def _hash(self, text):
            return "old-hash"

        def _transition_auto(self, target):
            self.auto_machine.transition(target)
            return True

        def _set_status(self, *args):
            self.status = args

        def _schedule_auto(self, delay, callback):
            self.scheduled = (delay, callback)

        def _auto_clipboard_timeout(self):
            pass

        def _stop_auto(self, reason):
            raise AssertionError(reason)

    fake = FakeApp()
    ClipboardAgentApp._auto_click_copy(fake)
    assert fake.auto_state == AutoState.WAITING_INITIAL_CLIPBOARD
    assert fake.desktop.clicked == ScreenPoint(100, 200)


def test_copy_after_normal_visual_wait_enters_normal_waiting_clipboard():
    from types import SimpleNamespace
    from clipboard_agent.state_machine import AutoState, AutoStateMachine
    from clipboard_agent.win32_input import ScreenPoint

    class Desktop:
        def clipboard_sequence_number(self):
            return 13

        def click(self, point):
            self.clicked = point

    class FakeApp:
        auto_enabled = True
        auto_paused = False
        user_intervention = SimpleNamespace(is_set=lambda: False)
        auto_machine = AutoStateMachine(AutoState.WAITING_VISUAL)
        desktop = Desktop()
        settings = SimpleNamespace(auto_clipboard_timeout_seconds=1.5)

        @property
        def auto_state(self):
            return self.auto_machine.state

        def _auto_find_copy_button(self):
            match = SimpleNamespace(confidence=0.99)
            return ScreenPoint(120, 220), match

        def _read_clipboard(self):
            return "old"

        def _hash(self, text):
            return "old-hash"

        def _transition_auto(self, target):
            self.auto_machine.transition(target)
            return True

        def _set_status(self, *args):
            self.status = args

        def _schedule_auto(self, delay, callback):
            self.scheduled = (delay, callback)

        def _auto_clipboard_timeout(self):
            pass

        def _stop_auto(self, reason):
            raise AssertionError(reason)

    fake = FakeApp()
    ClipboardAgentApp._auto_click_copy(fake)
    assert fake.auto_state == AutoState.WAITING_CLIPBOARD
    assert fake.desktop.clicked == ScreenPoint(120, 220)


def test_new_project_clears_last_result_identity(monkeypatch):
    class Executor:
        running = False

    class Var:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class Text:
        def __init__(self, value=""):
            self.value = value

        def get(self, *_args):
            return self.value

        def delete(self, *_args):
            self.value = ""

    class Widget:
        def configure(self, **_kwargs):
            pass

    class History:
        def get_children(self):
            return ["row1"]

        def delete(self, _item):
            pass

    monkeypatch.setattr("clipboard_agent.app.messagebox.askyesno", lambda *a, **k: True)

    class FakeApp:
        executor = Executor()
        auto_enabled = False
        project_var = Var("C:/project")
        execution_count = 1
        goal_text = Text("goal")
        pending_request = object()
        pending_cwd = Path(".")
        pending_kind = None
        last_result_text = "#ExecutionResult\nID: old"
        last_result_request_id = "old"
        processed_agent_hashes = {"hash"}
        processed_request_ids = {"old"}
        session_info = Widget()
        history = History()
        terminal = Text("log")

        def _clear_command_panel(self):
            pass

        def _save_settings(self):
            pass

        def _set_status(self, *args):
            self.status = args

        def _choose_project(self):
            pass

        def after(self, _delay, _callback):
            # Do not open a native folder dialog in this unit test.
            pass

    fake = FakeApp()
    ClipboardAgentApp._new_project(fake)
    assert fake.last_result_text == ""
    assert fake.last_result_request_id == ""
    assert fake.processed_request_ids == set()
    assert fake.processed_agent_hashes == set()
