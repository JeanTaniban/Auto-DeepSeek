from __future__ import annotations

from pathlib import Path

from ..prompt_builder import build_initial_prompt as build_generic_prompt
from .base import AgentProfile
from .models import DetectionResult, HealthCheck, HealthReport, HealthStatus, ProfileMetadata, ProfileState


class GenericProfile(AgentProfile):
    _metadata = ProfileMetadata(
        profile_id="generic",
        display_name="Développement général",
        version="1",
        description="Profil générique historique Auto-DeepSeek V2.14.",
    )

    @property
    def metadata(self) -> ProfileMetadata:
        return self._metadata

    def detect_project(self, project_root: Path) -> DetectionResult:
        # GenericProfile is the deterministic fallback, not a specialized
        # detector. Keep a minimal confidence so any future specialized profile
        # can outrank it without special casing in ProfileManager.
        return DetectionResult(
            matched=project_root.exists() and project_root.is_dir(),
            confidence=0.01,
            reasons=("Profil générique de secours.",),
            project_type="generic",
        )

    def health_check(self, project_root: Path, *, deep: bool = False) -> HealthReport:
        del deep
        if not project_root.exists() or not project_root.is_dir():
            return HealthReport((
                HealthCheck(
                    check_id="project-root",
                    status=HealthStatus.FAIL,
                    message="La racine projet n'existe pas ou n'est pas un dossier.",
                ),
            ))
        return HealthReport((
            HealthCheck(
                check_id="project-root",
                status=HealthStatus.PASS,
                message="Racine projet accessible.",
            ),
        ))

    def current_state(self) -> ProfileState:
        return ProfileState.READY

    def build_initial_prompt(
        self,
        project_root: Path,
        goal: str,
        *,
        shell: str,
        os_name: str,
    ) -> str:
        # P0 invariant: the generic prompt must remain byte-for-byte identical
        # to the V2.14 prompt builder output.
        return build_generic_prompt(
            project_root,
            goal,
            shell=shell,
            os_name=os_name,
        )
