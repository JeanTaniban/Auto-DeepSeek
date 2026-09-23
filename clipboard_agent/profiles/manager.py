from __future__ import annotations

from pathlib import Path

from .base import AgentProfile
from .models import DetectionResult
from .registry import ProfileRegistry, ProfileRegistryError
from .tools import ToolDescriptor, ToolExecutionError, ToolRequest, ToolResult


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

    def tool_descriptors(self, project_root: Path) -> tuple[ToolDescriptor, ...]:
        return self.active_profile.tool_descriptors(project_root)

    def execute_tool(self, request: ToolRequest, project_root: Path) -> ToolResult:
        if request.profile_id != self.active_profile_id:
            raise ToolExecutionError(
                f"Outil refusé : profil actif {self.active_profile_id!r}, requête pour {request.profile_id!r}."
            )

        profile = self.active_profile
        available = {item.tool_id: item for item in self.tool_descriptors(project_root)}
        descriptor = available.get(request.tool_id)
        if descriptor is None:
            raise ToolExecutionError(
                f"Outil {request.tool_id!r} non disponible dans le profil {self.active_profile_id!r}."
            )
        if descriptor.provider != request.provider:
            raise ToolExecutionError(
                f"Provider incohérent pour {request.tool_id!r} : attendu {descriptor.provider!r}, reçu {request.provider!r}."
            )

        profile.prepare_tool(request, project_root)

        # Preparation can change health/capability state. Re-read the descriptor
        # so a profile cannot execute a stale capability contract.
        refreshed = {item.tool_id: item for item in self.tool_descriptors(project_root)}
        descriptor = refreshed.get(request.tool_id)
        if descriptor is None:
            raise ToolExecutionError(
                f"Outil {request.tool_id!r} devenu indisponible après préparation du profil."
            )
        if descriptor.provider != request.provider:
            raise ToolExecutionError(
                f"Provider incohérent après préparation pour {request.tool_id!r}."
            )

        state = profile.current_state()
        if state not in descriptor.allowed_states:
            allowed = ", ".join(item.value for item in descriptor.allowed_states) or "<aucun>"
            raise ToolExecutionError(
                f"Outil {request.tool_id!r} interdit dans l'état profil {state.value}; "
                f"états autorisés : {allowed}."
            )
        return profile.execute_tool(request, project_root)
