from pathlib import Path

import pytest

from clipboard_agent.models import ExecutionStatus
from clipboard_agent.profiles import (
    AgentProfile,
    DetectionResult,
    HealthReport,
    ProfileManager,
    ProfileMetadata,
    ProfileRegistry,
    ProfileState,
    ToolDescriptor,
    ToolExecutionError,
    ToolRequest,
    ToolResult,
)


class StatefulToolProfile(AgentProfile):
    def __init__(self, state=ProfileState.UNINITIALIZED):
        self._state = state
        self.prepared = 0

    @property
    def metadata(self):
        return ProfileMetadata("stateful", "Stateful", "1")

    def detect_project(self, project_root: Path):
        return DetectionResult(True, self.confidence if hasattr(self, "confidence") else 1.0, project_type="stateful")

    def health_check(self, project_root: Path, *, deep: bool = False):
        del project_root, deep
        self._state = ProfileState.READY
        return HealthReport()

    def current_state(self):
        return self._state

    def build_initial_prompt(self, project_root: Path, goal: str, *, shell: str, os_name: str):
        return "stateful"

    def tool_descriptors(self, project_root: Path):
        del project_root
        return (ToolDescriptor(tool_id="demo", provider="demo", description="Demo tool"),)

    def prepare_tool(self, request: ToolRequest, project_root: Path):
        del request, project_root
        self.prepared += 1
        if self._state == ProfileState.UNINITIALIZED:
            self._state = ProfileState.READY

    def execute_tool(self, request: ToolRequest, project_root: Path):
        del project_root
        return ToolResult(
            request_id=request.request_id,
            profile_id="stateful",
            provider="demo",
            tool_id="demo",
            status=ExecutionStatus.SUCCESS,
            profile_state=self._state,
        )


def _request():
    return ToolRequest(
        request_id="tool-1",
        profile_id="stateful",
        provider="demo",
        tool_id="demo",
    )


def test_manager_prepares_uninitialized_profile_before_state_validation(tmp_path):
    profile = StatefulToolProfile(ProfileState.UNINITIALIZED)
    manager = ProfileManager(ProfileRegistry((profile,)), active_profile_id="stateful", fallback_profile_id="stateful")
    result = manager.execute_tool(_request(), tmp_path)
    assert result.status == ExecutionStatus.SUCCESS
    assert profile.prepared == 1
    assert profile.current_state() == ProfileState.READY


def test_manager_rejects_tool_when_profile_remains_busy(tmp_path):
    profile = StatefulToolProfile(ProfileState.BUSY)
    manager = ProfileManager(ProfileRegistry((profile,)), active_profile_id="stateful", fallback_profile_id="stateful")
    with pytest.raises(ToolExecutionError, match="état profil BUSY"):
        manager.execute_tool(_request(), tmp_path)


def test_manager_rejects_tool_when_user_action_is_required(tmp_path):
    profile = StatefulToolProfile(ProfileState.USER_ACTION_REQUIRED)
    manager = ProfileManager(ProfileRegistry((profile,)), active_profile_id="stateful", fallback_profile_id="stateful")
    with pytest.raises(ToolExecutionError, match="USER_ACTION_REQUIRED"):
        manager.execute_tool(_request(), tmp_path)
