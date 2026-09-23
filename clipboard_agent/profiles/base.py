from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .models import DetectionResult, HealthReport, ProfileMetadata, ProfileState


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

    def shutdown(self) -> None:
        """Best-effort profile cleanup hook. P0 profiles have nothing to release."""
