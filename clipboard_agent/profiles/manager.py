from __future__ import annotations

from pathlib import Path

from .base import AgentProfile
from .models import DetectionResult
from .registry import ProfileRegistry, ProfileRegistryError


class ProfileManager:
    def __init__(
        self,
        registry: ProfileRegistry,
        *,
        active_profile_id: str = "generic",
        fallback_profile_id: str = "generic",
    ) -> None:
        self.registry = registry
        if not registry.contains(fallback_profile_id):
            raise ProfileRegistryError(
                f"Profil de secours absent du registre : {fallback_profile_id}"
            )
        self.fallback_profile_id = fallback_profile_id
        self._active_profile_id = (
            active_profile_id if registry.contains(active_profile_id) else fallback_profile_id
        )

    @property
    def active_profile(self) -> AgentProfile:
        return self.registry.get(self._active_profile_id)

    @property
    def active_profile_id(self) -> str:
        return self._active_profile_id

    def select(self, profile_id: str) -> AgentProfile:
        profile = self.registry.get(profile_id)
        if profile_id == self._active_profile_id:
            return profile
        self.active_profile.shutdown()
        self._active_profile_id = profile_id
        return profile

    def select_by_display_name(self, display_name: str) -> AgentProfile:
        profile = self.registry.get_by_display_name(display_name)
        return self.select(profile.metadata.profile_id)

    def detect(self, project_root: Path) -> tuple[tuple[AgentProfile, DetectionResult], ...]:
        matches: list[tuple[AgentProfile, DetectionResult]] = []
        for profile in self.registry.profiles():
            result = profile.detect_project(project_root)
            if result.matched:
                matches.append((profile, result))
        matches.sort(key=lambda item: item[1].confidence, reverse=True)
        return tuple(matches)

    def suggested_profile(self, project_root: Path) -> AgentProfile:
        matches = self.detect(project_root)
        if matches:
            return matches[0][0]
        return self.registry.get(self.fallback_profile_id)
