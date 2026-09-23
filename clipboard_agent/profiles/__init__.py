from __future__ import annotations

from .base import AgentProfile
from .generic import GenericProfile
from .manager import ProfileManager
from .models import DetectionResult, HealthCheck, HealthReport, HealthStatus, ProfileMetadata, ProfileState
from .registry import ProfileRegistry, ProfileRegistryError


def build_default_profile_registry() -> ProfileRegistry:
    """Return the profiles shipped by this build.

    P0 intentionally registers only GenericProfile. Unity is introduced in
    later phases so the current Relay behavior remains the only executable
    profile until its specialized provider is implemented and validated.
    """
    return ProfileRegistry((GenericProfile(),))


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
    "build_default_profile_registry",
]
