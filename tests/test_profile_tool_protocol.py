from pathlib import Path

import pytest

from clipboard_agent.models import ExecutionStatus
from clipboard_agent.profile_tool_protocol import ProfileToolProtocolError, parse_profile_tool_request
from clipboard_agent.profile_tool_runtime import format_tool_result
from clipboard_agent.profiles import ProfileState, ToolResult


def test_parse_profile_tool_request_with_json_payload():
    request = parse_profile_tool_request("""#Relay
Protocol: 2
Action: TOOL
ID: unity-observe-1
Profile: unity
Provider: unity-visual
Tool: unity.observe
Timeout: 45

{"intent":"GAME"}
""")
    assert request is not None
    assert request.request_id == "unity-observe-1"
    assert request.profile_id == "unity"
    assert request.provider == "unity-visual"
    assert request.tool_id == "unity.observe"
    assert request.arguments == {"intent": "GAME"}
    assert request.timeout == 45


def test_non_tool_relay_is_ignored_by_profile_parser():
    assert parse_profile_tool_request("""#Relay
Protocol: 2
Action: END
ID: done
""") is None


def test_tool_requires_strict_single_block_and_object_json():
    with pytest.raises(ProfileToolProtocolError, match="unique bloc"):
        parse_profile_tool_request("Intro\n```text\n#Relay\nProtocol: 2\nAction: TOOL\nID: x\nProfile: unity\nProvider: unity-cli\nTool: unity.recompile\n```")
    with pytest.raises(ProfileToolProtocolError, match="objet JSON"):
        parse_profile_tool_request("""#Relay
Protocol: 2
Action: TOOL
ID: x
Profile: unity
Provider: unity-cli
Tool: unity.recompile

[]
""")


def test_format_tool_result_exposes_machine_readable_profile_evidence():
    result = ToolResult(
        request_id="unity-observe-1",
        profile_id="unity",
        provider="unity-visual",
        tool_id="unity.observe",
        status=ExecutionStatus.SUCCESS,
        profile_state=ProfileState.READY,
        data={"confidence": "HIGH", "source": "GAME_VIEW_NATIVE"},
        artifacts=(str(Path("frame.png")),),
        recommended_next=("TOOL", "EXECUTION"),
    )
    text = format_tool_result(result)
    assert text.startswith("#RelayResult\nProtocol: 2\nKind: TOOL")
    assert "Profile: unity" in text
    assert "Tool: unity.observe" in text
    assert "confidence" in text and "GAME_VIEW_NATIVE" in text
    assert "Artifacts: frame.png" in text
