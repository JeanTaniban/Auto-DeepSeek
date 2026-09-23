from pathlib import Path
import inspect

import cv2
import numpy as np

import clipboard_agent.profiled_app as profiled_app_module
from clipboard_agent.models import ExecutionStatus
from clipboard_agent.profile_tool_host import ProfileToolHostMixin
from clipboard_agent.profiled_app import ProfiledClipboardAgentApp
from clipboard_agent.profiles import ProfileState, ToolResult


def test_profiled_app_uses_tool_host_mixin_before_core_app():
    mro = ProfiledClipboardAgentApp.__mro__
    assert ProfileToolHostMixin in mro
    assert mro.index(ProfileToolHostMixin) < mro.index(profiled_app_module.ClipboardAgentApp)


def test_profiled_app_does_not_embed_tool_protocol_runtime_logic():
    source = inspect.getsource(profiled_app_module)
    assert "parse_profile_tool_request" not in source
    assert "ProfileToolRunner" not in source
    assert "format_tool_result" not in source


def test_profile_tool_host_loads_first_valid_image_artifact(tmp_path: Path):
    invalid = tmp_path / "missing.png"
    image_path = tmp_path / "frame.png"
    image = np.zeros((8, 12, 3), dtype=np.uint8)
    image[:, :, 1] = 180
    assert cv2.imwrite(str(image_path), image)

    result = ToolResult(
        request_id="observe-1",
        profile_id="unity",
        provider="unity-visual",
        tool_id="unity.observe",
        status=ExecutionStatus.SUCCESS,
        profile_state=ProfileState.READY,
        artifacts=(str(invalid), str(image_path)),
    )

    attachment = ProfileToolHostMixin._profile_attachment(result)
    assert attachment is not None
    assert attachment.shape == (8, 12, 3)
    assert int(attachment[0, 0, 1]) == 180
