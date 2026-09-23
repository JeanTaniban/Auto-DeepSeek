from types import SimpleNamespace

from clipboard_agent.profiled_app import ProfiledClipboardAgentApp
from clipboard_agent.storage import Settings


def test_prompt_teaches_system_error_only_when_auto_repair_self_is_enabled(tmp_path):
    goal = SimpleNamespace(get=lambda *_args: "Ship it")

    enabled = SimpleNamespace(
        settings=Settings(profile_id="generic", auto_repair_self=True),
        goal_text=goal,
        _project_root=lambda: tmp_path,
    )
    enabled_manager = ProfiledClipboardAgentApp._ensure_profile_manager(enabled)
    enabled._ensure_profile_manager = lambda: enabled_manager
    enabled_prompt = ProfiledClipboardAgentApp._build_prompt(enabled)
    assert "Auto repair self / SYSTEM_ERROR" in enabled_prompt
    assert "Kind: SYSTEM_ERROR" in enabled_prompt
    assert "Ne contourne jamais" in enabled_prompt

    disabled = SimpleNamespace(
        settings=Settings(profile_id="generic", auto_repair_self=False),
        goal_text=goal,
        _project_root=lambda: tmp_path,
    )
    disabled_manager = ProfiledClipboardAgentApp._ensure_profile_manager(disabled)
    disabled._ensure_profile_manager = lambda: disabled_manager
    disabled_prompt = ProfiledClipboardAgentApp._build_prompt(disabled)
    assert "Auto repair self / SYSTEM_ERROR" not in disabled_prompt
