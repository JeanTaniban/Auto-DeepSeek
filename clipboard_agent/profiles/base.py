from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .models import DetectionResult, HealthReport, ProfileMetadata, ProfileState
from .tools import ToolDescriptor, ToolExecutionError, ToolRequest, ToolResult


class AgentProfile(ABC):
    """Stable profile boundary between the generic Relay core and domain adapters."""

    @property
    @abstractmethod
    def metadata(self) -> ProfileMetadata:
        raise NotImplementedError

    @abstractmethod
    def detect_project(self, project_root: Path) -> DetectionResult:
        raise NotImplementedError

    @abstractmethod
    def health_check(self, project_root: Path, *, deep: bool = False) -> HealthReport:
        raise NotImplementedError

    @abstractmethod
    def current_state(self) -> ProfileState:
        raise NotImplementedError

    @abstractmethod
    def build_initial_prompt(
        self,
        project_root: Path,
        goal: str,
        *,
        shell: str,
        os_name: str,
    ) -> str:
        raise NotImplementedError

    def tool_descriptors(self, project_root: Path) -> tuple[ToolDescriptor, ...]:
        """Return tools currently exposed by this profile.

        P0/GenericProfile exposes no semantic tools. Specialized profiles can
        opt in incrementally without forcing domain-specific code into the
        generic Relay application.
        """
        del project_root
        return ()

    def execute_tool(self, request: ToolRequest, project_root: Path) -> ToolResult:
        del request, project_root
        raise ToolExecutionError(
            f"Le profil {self.metadata.profile_id!r} n'expose aucun outil exécutable."
        )

    def shutdown(self) -> None:
        """Best-effort profile cleanup hook."""
