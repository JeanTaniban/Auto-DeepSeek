from pathlib import Path
from types import SimpleNamespace

from clipboard_agent.profiled_app import ProfiledClipboardAgentApp
from clipboard_agent.prompt_builder import build_initial_prompt
from clipboard_agent.storage import Settings
from clipboard_agent.test_session import TestSessionState


class _GoalText:
    def __init__(self, value: str):
        self.value = value

    def get(self, *_args):
        return self.value


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
        test_session=SimpleNamespace(state=TestSessionState.CLOSED),
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
            values[field] = SimpleNamespace(state=TestSessionState.ACTIVE_BACKGROUND)
        assert ProfiledClipboardAgentApp._profile_switch_blocked(SimpleNamespace(**values)) is True
