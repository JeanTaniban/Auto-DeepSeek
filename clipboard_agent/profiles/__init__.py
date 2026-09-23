from __future__ import annotations

from .base import AgentProfile
from .generic import GenericProfile
from .manager import ProfileManager
from .models import DetectionResult, HealthCheck, HealthReport, HealthStatus, ProfileMetadata, ProfileState
from .registry import ProfileRegistry, ProfileRegistryError
from .tools import ToolDescriptor, ToolExecutionError, ToolNature, ToolRequest, ToolResult
from .unity import UnityProfile


def build_default_profile_registry() -> ProfileRegistry:
    """Return the profiles shipped by this build."""
    return ProfileRegistry((GenericProfile(), UnityProfile()))


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
