from pathlib import Path
from types import SimpleNamespace

import numpy as np

from clipboard_agent.app import ClipboardAgentApp
from clipboard_agent.models import AgentDirective, DirectiveKind, ExecutionRequest, ExecutionStatus, InteractionAction, InteractionKind
from clipboard_agent.state_machine import AutoState, AutoStateMachine
from clipboard_agent.target_session import TargetObservation, TargetSessionResult


class _Widget:
    def configure(self, **_kwargs):
        pass


class _History:
    def insert(self, *_args, **_kwargs):
        pass


class _Goal:
    def get(self, *_args):
        return "goal"


def test_handle_multiple_auto_schedules_target_session(tmp_path: Path):
    directive = AgentDirective(
        kind=DirectiveKind.MULTIPLE,
        request=ExecutionRequest("python app.py", shell="bash", request_id="m1"),
        actions=(InteractionAction(InteractionKind.OBSERVE, label="initial"),),
    )

    class FakeApp:
        auto_enabled = True
        auto_paused = False
        pending_request = None
        pending_cwd = None
        pending_kind = DirectiveKind.EXECUTION
        pending_actions = ()
        run_btn = _Widget()
        refuse_btn = _Widget()

        def _show_multiple_command(self, *args):
            self.shown = args

        def _set_status(self, *args):
            self.status = args

        def _schedule_auto(self, delay, callback):
            self.scheduled = (delay, callback)

        def _pause_auto(self, reason):
            raise AssertionError(reason)

        def _stop_auto(self, reason, set_status=True):
            raise AssertionError(reason)

        def _run_pending(self):
            pass

    fake = FakeApp()
    ClipboardAgentApp._handle_multiple(fake, directive, tmp_path, source_auto=True)
    assert fake.pending_kind == DirectiveKind.MULTIPLE
    assert fake.pending_actions == directive.actions
    assert fake.scheduled[0] == 100


def test_finalize_target_result_transitions_to_restore_and_sends_sheet():
    image = np.zeros((80, 120, 3), dtype=np.uint8)
    result = TargetSessionResult(
        request_id="m2",
        status=ExecutionStatus.SUCCESS,
        duration=1.0,
        target_title="Demo",
        client_width=120,
        client_height=80,
        actions_total=1,
        actions_completed=1,
        observations=[TargetObservation(1, "initial", image, 120, 80)],
        browser_restored=True,
    )

    class Event:
        def is_set(self):
            return False

    class FakeApp:
        stop_btn = _Widget()
        pending_request = object()
        pending_cwd = Path(".")
        pending_kind = DirectiveKind.MULTIPLE
        pending_actions = (object(),)
        execution_count = 0
        session_info = _Widget()
        history = _History()
        goal_text = _Goal()
        settings = SimpleNamespace(goal_reminder_every=4)
        last_result_text = ""
        last_result_request_id = ""
        user_intervention = Event()
        auto_enabled = True
        auto_paused = False
        auto_machine = AutoStateMachine(AutoState.TARGET_RUNNING)
        target_browser_snapshot = object()

        @property
        def auto_state(self):
            return self.auto_machine.state

        def _append_terminal(self, *_args):
            pass

        def _transition_auto(self, target):
            self.auto_machine.transition(target)
            return True

        def _set_status(self, *args):
            self.status = args

        def _schedule_auto_action(self, name, callback):
            self.scheduled = (name, callback)

        def _auto_send_message(self, text, image_bgr=None):
            self.sent = (text, image_bgr)

        def _write_clipboard(self, _text):
            raise AssertionError("Auto success should send through browser, not fall back to manual clipboard")

        def _stop_auto(self, *_args, **_kwargs):
            raise AssertionError("Auto should continue")

    fake = FakeApp()
    ClipboardAgentApp._finalize_target_result(fake, result)
    assert fake.auto_state == AutoState.TARGET_RESTORING
    assert fake.scheduled[0] == "auto_delay_result_to_send_seconds"
    fake.scheduled[1]()
    assert "#MultipleResult" in fake.sent[0]
    assert fake.sent[1] is not None
    assert fake.sent[1].shape[0] > image.shape[0]


def test_initial_duplicate_multiple_reuses_matching_result_and_image():
    text = (
        "#Multiple\n"
        "ID: multi-resume\n"
        "Shell: powershell\n"
        "CWD: .\n"
        "Timeout: 30\n"
        "Launch: python app.py\n\n"
        "#Observe final"
    )
    image = np.zeros((30, 40, 3), dtype=np.uint8)
    digest = ClipboardAgentApp._hash(text)

    class Executor:
        running = False

    class TargetRunner:
        running = False

    class FakeApp:
        executor = Executor()
        target_runner = TargetRunner()
        auto_machine = AutoStateMachine(AutoState.PROCESSING_INITIAL_REPLY)
        processed_agent_hashes = {digest}
        processed_request_ids = {"multi-resume"}
        last_result_request_id = "multi-resume"
        last_result_text = "#MultipleResult\nProtocol: 1\nID: multi-resume\nStatus: SUCCESS"
        last_result_kind = DirectiveKind.MULTIPLE
        last_result_attachment = image

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
            return 25

        def _schedule_auto(self, delay, callback):
            self.scheduled = (delay, callback)

        def _set_status(self, *args):
            self.status = args

        def _append_terminal(self, *args):
            pass

        def _auto_send_message(self, value, attachment=None):
            self.sent = (value, attachment)

        def _auto_resume_from_last_result(self, directive):
            return ClipboardAgentApp._auto_resume_from_last_result(self, directive)

        def _stop_auto(self, reason):
            raise AssertionError(f"Auto must resume target result instead of stopping: {reason}")

    fake = FakeApp()
    ClipboardAgentApp._handle_agent_text(fake, text, source_auto=True)
    assert fake.auto_state == AutoState.RECOVERING_LAST_RESULT
    fake.scheduled[1]()
    assert fake.sent[0] == fake.last_result_text
    assert fake.sent[1] is image


def test_target_result_after_user_takeover_never_touches_clipboard_even_if_auto_already_stopped():
    result = TargetSessionResult(
        request_id="m-user",
        status=ExecutionStatus.CANCELLED,
        duration=0.5,
        target_title="Demo",
        client_width=320,
        client_height=200,
        actions_total=3,
        actions_completed=1,
        observations=[],
        browser_restored=False,
        target_left_open=True,
        note="user takeover",
    )

    class Event:
        def is_set(self):
            # _stop_auto() may already have cleared the generic event.
            return False

    class FakeApp:
        stop_btn = _Widget()
        pending_request = object()
        pending_cwd = Path(".")
        pending_kind = DirectiveKind.MULTIPLE
        pending_actions = (object(),)
        execution_count = 0
        session_info = _Widget()
        history = _History()
        goal_text = _Goal()
        settings = SimpleNamespace(goal_reminder_every=4)
        last_result_text = ""
        last_result_request_id = ""
        last_result_kind = None
        last_result_attachment = None
        user_intervention = Event()
        target_interrupted_by_user = True
        auto_enabled = False
        auto_paused = False

        def _append_terminal(self, *_args):
            pass

        def _set_status(self, *args):
            self.status = args

        def _write_clipboard(self, _text):
            raise AssertionError("user takeover must suppress late clipboard writes")

    fake = FakeApp()
    ClipboardAgentApp._finalize_target_result(fake, result)
    assert fake.target_interrupted_by_user is False
    assert fake.last_result_request_id == "m-user"
    assert fake.status[0] == "TARGET APP ARRÊTÉE"
