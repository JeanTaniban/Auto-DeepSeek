from __future__ import annotations

from .base import AgentProfile
from .generic import GenericProfile
from .manager import ProfileManager
from .models import DetectionResult, HealthCheck, HealthReport, HealthStatus, ProfileMetadata, ProfileState
from .registry import ProfileRegistry, ProfileRegistryError
from .tools import ToolDescriptor, ToolExecutionError, ToolNature, ToolRequest, ToolResult
from .unity import UnityProfile


def build_default_profile_registry(*, desktop=None) -> ProfileRegistry:
    """Return shipped profiles, optionally wiring local UI capabilities.

    The generic core passes only infrastructure abstractions (for example the
    Win32 desktop object). Unity-specific provider selection remains contained
    in the Unity profile package.
    """
    unity = UnityProfile()
    if desktop is not None:
        from .unity.visual import UnityVisualRouter
        from .unity.windows_visual import UnityEditorWindowVisualProvider

        unity.visual_router = UnityVisualRouter((UnityEditorWindowVisualProvider(desktop),))
    return ProfileRegistry((GenericProfile(), unity))


__all__ = [
    "AgentProfile",
    "DetectionResult",
    "GenericProfile",
    "HealthCheck",
    "HealthReport",
    "HealthStatus",
    "ProfileManager",
    "ProfileMetadata",
    "ProfileRegistry",
    "ProfileRegistryError",
    "ProfileState",
    "ToolDescriptor",
    "ToolExecutionError",
    "ToolNature",
    "ToolRequest",
    "ToolResult",
    "UnityProfile",
    "build_default_profile_registry",
]
