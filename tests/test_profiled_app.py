import threading
from pathlib import Path
from types import SimpleNamespace

from clipboard_agent.app import ClipboardAgentApp
from clipboard_agent.models import AgentDirective, DirectiveKind, ExecutionRequest, ExecutionStatus, InteractionAction, InteractionKind
from clipboard_agent.profiled_app import ProfiledClipboardAgentApp
from clipboard_agent.prompt_builder import build_initial_prompt
from clipboard_agent.state_machine import AutoState
from clipboard_agent.storage import Settings
from clipboard_agent.test_session import TestSessionResult, TestSessionState as SessionState


class _GoalText:
    def __init__(self, value: str):
        self.value = value

    def get(self, *_args):
        return self.value


class _Session:
    def __init__(self, state=SessionState.CLOSED, session_id="old-session", busy=False):
        self.state = state
        self.session_id = session_id
        self.busy = busy
        self.force_close_calls = 0

    def force_close(self):
        self.force_close_calls += 1
        self.state = SessionState.CLOSED
        self.session_id = ""


class _Workspace:
    def __init__(self, restore=True):
        self.binding = object()
        self.restore = restore
        self.restore_calls = 0
        self.last_error = None if restore else "focus failed"

    def restore_llm_workspace(self):
        self.restore_calls += 1
        return self.restore


def _bare_app(state=SessionState.CLOSED):
    app = object.__new__(ProfiledClipboardAgentApp)
    app.test_session = _Session(state)
    app.workspace = _Workspace()
    app.auto_enabled = True
    app.auto_paused = False
    app.user_intervention = threading.Event()
    app.test_interrupted_by_user = False
    app.logs = []
    app.stop_reason = None
    app._append_terminal = lambda text, tag=None: app.logs.append((text, tag))
    app._stop_auto = lambda reason, **_kwargs: setattr(app, "stop_reason", reason)
    return app


def test_profiled_app_falls_back_to_generic_without_tk_instance():
    fake = SimpleNamespace(settings=Settings(profile_id="removed-profile"))
    manager = ProfiledClipboardAgentApp._ensure_profile_manager(fake)
    assert manager.active_profile_id == "generic"
    assert fake.settings.profile_id == "generic"


def test_profiled_app_build_prompt_routes_through_active_profile(tmp_path: Path):
    fake = SimpleNamespace(
        settings=Settings(profile_id="generic"),
        goal_text=_GoalText("Ship it"),
        _project_root=lambda: tmp_path,
    )
    manager = ProfiledClipboardAgentApp._ensure_profile_manager(fake)
    fake._ensure_profile_manager = lambda: manager

    actual = ProfiledClipboardAgentApp._build_prompt(fake)
    expected = build_initial_prompt(
        tmp_path,
        "Ship it",
        shell="powershell" if __import__("os").name == "nt" else "bash",
        os_name=__import__("platform").system(),
    )
    assert actual == expected


def test_profile_switch_is_blocked_by_any_active_runtime():
    base = dict(
        auto_enabled=False,
        executor=SimpleNamespace(running=False),
        target_runner=SimpleNamespace(running=False),
        test_session=SimpleNamespace(state=SessionState.CLOSED),
    )
    fake = SimpleNamespace(**base)
    assert ProfiledClipboardAgentApp._profile_switch_blocked(fake) is False

    for field in ("auto_enabled", "executor", "target_runner", "test_session"):
        values = dict(base)
        if field == "auto_enabled":
            values[field] = True
        elif field == "executor":
            values[field] = SimpleNamespace(running=True)
        elif field == "target_runner":
            values[field] = SimpleNamespace(running=True)
        else:
            values[field] = SimpleNamespace(state=SessionState.ACTIVE_BACKGROUND)
        assert ProfiledClipboardAgentApp._profile_switch_blocked(SimpleNamespace(**values)) is True


def test_auto_dispose_closes_stale_session_and_restores_llm():
    app = _bare_app(SessionState.ACTIVE_BACKGROUND)

    assert app._auto_dispose_test_session("fresh intent") is True
    assert app.test_session.force_close_calls == 1
    assert app.test_session.state == SessionState.CLOSED
    assert app.workspace.restore_calls == 1
    assert app.stop_reason is None
    assert "fresh intent" in app.logs[0][0]


def test_new_open_replaces_existing_session_before_base_handler(monkeypatch, tmp_path):
    app = _bare_app(SessionState.ACTIVE_BACKGROUND)
    calls = []

    def base_handler(self, directive, cwd, source_auto):
        calls.append((self.test_session.state, directive.request.request_id, cwd, source_auto))

    monkeypatch.setattr(ClipboardAgentApp, "_handle_open_test_session", base_handler)
    directive = AgentDirective(
        DirectiveKind.OPEN_TEST_SESSION,
        request=ExecutionRequest("python app.py", request_id="fresh-open"),
    )

    app._handle_open_test_session(directive, tmp_path, True)

    assert app.test_session.force_close_calls == 1
    assert calls == [(SessionState.CLOSED, "fresh-open", tmp_path, True)]
    assert app.stop_reason is None


def test_execution_cleans_persistent_session_before_normal_routing(monkeypatch, tmp_path):
    app = _bare_app(SessionState.ACTIVE_BACKGROUND)
    calls = []

    def base_handler(self, request, cwd, source_auto):
        calls.append((self.test_session.state, request.request_id, cwd, source_auto))

    monkeypatch.setattr(ClipboardAgentApp, "_handle_execution", base_handler)
    request = ExecutionRequest("git status", request_id="exec-after-ui")

    app._handle_execution(request, tmp_path, True)

    assert app.test_session.force_close_calls == 1
    assert calls == [(SessionState.CLOSED, "exec-after-ui", tmp_path, True)]


def test_close_on_already_closed_session_is_idempotent_and_does_not_stop_auto():
    app = _bare_app(SessionState.CLOSED)
    transitions = []
    results = []
    app._transition_auto = lambda state: transitions.append(state) or True
    app._finalize_test_session_result = results.append

    app._handle_close_test_session(
        AgentDirective(DirectiveKind.CLOSE_TEST_SESSION, request_id="close-again"),
        True,
    )

    assert transitions == [AutoState.TEST_CLOSING]
    assert len(results) == 1
    assert results[0].status == ExecutionStatus.SUCCESS
    assert results[0].session_state == SessionState.CLOSED.value
    assert "idempotente" in results[0].note
    assert app.stop_reason is None


def test_test_actions_on_lost_session_returns_recoverable_closed_result():
    app = _bare_app(SessionState.LOST)
    transitions = []
    results = []
    app._transition_auto = lambda state: transitions.append(state) or True
    app._finalize_test_session_result = results.append
    directive = AgentDirective(
        DirectiveKind.TEST_ACTIONS,
        request_id="actions-after-lost",
        actions=(InteractionAction(InteractionKind.OBSERVE, label="screen"),),
    )

    app._handle_test_actions(directive, True)

    assert app.test_session.force_close_calls == 1
    assert transitions == [AutoState.TEST_ACTING]
    assert len(results) == 1
    assert results[0].status == ExecutionStatus.ERROR
    assert results[0].session_state == SessionState.CLOSED.value
    assert "LOST" in results[0].note
    assert app.stop_reason is None


def test_lost_result_is_normalized_to_closed_before_base_formatter(monkeypatch):
    app = _bare_app(SessionState.LOST)
    forwarded = []
    monkeypatch.setattr(
        ClipboardAgentApp,
        "_finalize_test_session_result",
        lambda self, result: forwarded.append(result),
    )
    result = TestSessionResult(
        request_id="open-failed",
        session_id="old-session",
        operation="OPENED",
        status=ExecutionStatus.ERROR,
        duration=0.2,
        note="window disappeared",
        session_active=False,
        llm_restored=True,
        session_state=SessionState.LOST.value,
    )

    app._finalize_test_session_result(result)

    assert app.test_session.force_close_calls == 1
    assert result.session_state == SessionState.CLOSED.value
    assert result.session_active is False
    assert "aucun CLOSE_TEST_SESSION" in result.note
    assert forwarded == [result]
