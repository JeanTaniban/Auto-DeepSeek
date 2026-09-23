from pathlib import Path

import pytest

from clipboard_agent.models import ExecutionStatus
from clipboard_agent.profiles import (
    ProfileManager,
    ProfileRegistry,
    ProfileState,
    ToolExecutionError,
    ToolRequest,
    UnityProfile,
)
from clipboard_agent.profiles.unity.cli import UnityCliResult
from clipboard_agent.profiles.unity.state import UnityProfileState


def _unity_project(tmp_path: Path) -> Path:
    (tmp_path / "Assets").mkdir()
    (tmp_path / "Packages").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.3.24f1\n",
        encoding="utf-8",
    )
    return tmp_path


class FakeCli:
    def __init__(self, result=None, *, available=True):
        self.result = result
        self.is_available = available
        self.recompile_calls = 0

    def available(self):
        return self.is_available

    def recompile(self, project_root, *, timeout=240):
        del project_root, timeout
        self.recompile_calls += 1
        if self.result is None:
            raise AssertionError("recompile should not have been called")
        return self.result


def _compile_request(request_id="compile-state"):
    return ToolRequest(
        request_id=request_id,
        profile_id="unity",
        provider="unity-cli",
        tool_id="unity.recompile",
    )


def _health_request(request_id="health-state"):
    return ToolRequest(
        request_id=request_id,
        profile_id="unity",
        provider="unity-profile",
        tool_id="unity.health",
    )


def _manager(profile):
    return ProfileManager(
        ProfileRegistry((profile,)),
        active_profile_id="unity",
        fallback_profile_id="unity",
    )


def test_first_unity_tool_runs_lazy_health_then_compile(tmp_path):
    root = _unity_project(tmp_path)
    cli = FakeCli(UnityCliResult(
        args=("unity", "recompile"),
        exit_code=0,
        stdout='{"success":true}',
        stderr="",
        data={"success": True},
        success=True,
    ))
    profile = UnityProfile(cli=cli)
    assert profile.unity_state == UnityProfileState.UNINITIALIZED

    result = _manager(profile).execute_tool(_compile_request(), root)

    assert result.status == ExecutionStatus.SUCCESS
    assert profile.unity_state == UnityProfileState.EDITOR_READY
    assert profile.current_state() == ProfileState.READY
    assert result.data["unityState"] == "EDITOR_READY"
    assert cli.recompile_calls == 1


def test_missing_cli_enters_user_action_required_and_blocks_tool(tmp_path):
    root = _unity_project(tmp_path)
    cli = FakeCli(available=False)
    profile = UnityProfile(cli=cli)

    with pytest.raises(ToolExecutionError, match="USER_ACTION_REQUIRED"):
        _manager(profile).execute_tool(_compile_request(), root)

    assert profile.unity_state == UnityProfileState.USER_ACTION_REQUIRED
    assert profile.current_state() == ProfileState.USER_ACTION_REQUIRED
    assert cli.recompile_calls == 0


def test_compile_error_is_recoverable_editor_ready_state(tmp_path):
    root = _unity_project(tmp_path)
    cli = FakeCli(UnityCliResult(
        args=("unity", "recompile"),
        exit_code=6,
        stdout='{"success":false,"errors":[{"message":"CS0103 missing"}]}',
        stderr="",
        data={"success": False, "errors": [{"message": "CS0103 missing"}]},
        success=False,
    ))
    profile = UnityProfile(cli=cli)

    result = _manager(profile).execute_tool(_compile_request("compile-error"), root)

    assert result.status == ExecutionStatus.ERROR
    assert result.data["outcome"] == "COMPILE_ERROR"
    assert profile.unity_state == UnityProfileState.EDITOR_READY
    assert profile.current_state() == ProfileState.READY


def test_compile_timeout_enters_error_and_requires_explicit_health_recovery(tmp_path):
    root = _unity_project(tmp_path)
    cli = FakeCli(UnityCliResult(
        args=("unity", "recompile"),
        exit_code=None,
        stdout="",
        stderr="",
        timed_out=True,
    ))
    profile = UnityProfile(cli=cli)
    manager = _manager(profile)

    result = manager.execute_tool(_compile_request("compile-timeout-state"), root)

    assert result.status == ExecutionStatus.TIMEOUT
    assert profile.unity_state == UnityProfileState.ERROR
    assert profile.current_state() == ProfileState.ERROR

    with pytest.raises(ToolExecutionError, match="état profil ERROR"):
        manager.execute_tool(_compile_request("compile-again"), root)

    health = manager.execute_tool(_health_request(), root)
    assert health.status == ExecutionStatus.SUCCESS
    assert health.data["overall"] == "PASS"
    assert profile.unity_state == UnityProfileState.READY
    assert profile.current_state() == ProfileState.READY
