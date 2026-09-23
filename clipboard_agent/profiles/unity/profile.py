from __future__ import annotations

from pathlib import Path

from ...models import ExecutionStatus, RiskLevel
from ...prompt_builder import build_initial_prompt as build_generic_prompt
from ..base import AgentProfile
from ..models import DetectionResult, HealthCheck, HealthReport, HealthStatus, ProfileMetadata, ProfileState
from ..tools import ToolDescriptor, ToolExecutionError, ToolNature, ToolRequest, ToolResult
from .cli import UnityCliRunner
from .compiler import CompileOutcome, UnityCompileCoordinator
from .discovery import read_unity_project
from .prompt import build_unity_prompt_suffix
from .state import CompletionBarrier, UnityProfileState
from .visual import UnityVisualRouter, VisualCaptureError, VisualIntent


class UnityProfile(AgentProfile):
    _metadata = ProfileMetadata(
        profile_id="unity",
        display_name="Unity",
        version="0.1",
        description="Profil Unity CLI/Pipeline orienté compilation et preuves visuelles.",
    )

    def __init__(
        self,
        *,
        cli: UnityCliRunner | None = None,
        visual_router: UnityVisualRouter | None = None,
    ) -> None:
        self.cli = cli or UnityCliRunner()
        self.compiler = UnityCompileCoordinator(self.cli)
        self.visual_router = visual_router or UnityVisualRouter()
        self._state = UnityProfileState.UNINITIALIZED

    @property
    def metadata(self) -> ProfileMetadata:
        return self._metadata

    def detect_project(self, project_root: Path) -> DetectionResult:
        info = read_unity_project(project_root)
        if info is None:
            return DetectionResult(matched=False, confidence=0.0, project_type="unity")
        reasons = (
            "ProjectSettings/ProjectVersion.txt présent.",
            "Packages/manifest.json présent.",
            "Assets/ présent.",
        )
        return DetectionResult(
            matched=True,
            confidence=0.99,
            reasons=reasons,
            project_type="unity",
            detected_version=info.version,
        )

    def health_check(self, project_root: Path, *, deep: bool = False) -> HealthReport:
        del deep
        checks: list[HealthCheck] = []
        info = read_unity_project(project_root)
        if info is None:
            self._state = UnityProfileState.ERROR
            return HealthReport((
                HealthCheck(
                    check_id="unity-project",
                    status=HealthStatus.FAIL,
                    message="La racine sélectionnée n'est pas un projet Unity complet.",
                ),
            ))
        checks.append(HealthCheck(
            check_id="unity-project",
            status=HealthStatus.PASS,
            message=f"Projet Unity détecté{f' ({info.version})' if info.version else ''}.",
            details={"version": info.version},
        ))
        if not self.cli.available():
            self._state = UnityProfileState.USER_ACTION_REQUIRED
            checks.append(HealthCheck(
                check_id="unity-cli",
                status=HealthStatus.USER_ACTION_REQUIRED,
                message="Unity CLI n'est pas disponible dans PATH.",
                remediation="Installer/activer Unity CLI puis relancer la vérification du profil.",
                risk="LOW",
            ))
            return HealthReport(tuple(checks))
        checks.append(HealthCheck(
            check_id="unity-cli",
            status=HealthStatus.PASS,
            message="Unity CLI disponible.",
        ))
        self._state = UnityProfileState.READY
        return HealthReport(tuple(checks))

    def current_state(self) -> ProfileState:
        mapping = {
            UnityProfileState.UNINITIALIZED: ProfileState.UNINITIALIZED,
            UnityProfileState.CHECKING: ProfileState.BUSY,
            UnityProfileState.READY: ProfileState.READY,
            UnityProfileState.EDITOR_READY: ProfileState.READY,
            UnityProfileState.USER_ACTION_REQUIRED: ProfileState.USER_ACTION_REQUIRED,
            UnityProfileState.DEGRADED: ProfileState.DEGRADED,
            UnityProfileState.ERROR: ProfileState.ERROR,
        }
        return mapping.get(self._state, ProfileState.BUSY)

    def tool_descriptors(self, project_root: Path) -> tuple[ToolDescriptor, ...]:
        if read_unity_project(project_root) is None:
            return ()
        return (
            ToolDescriptor(
                tool_id="unity.recompile",
                provider="unity-cli",
                description="Recompile les scripts C# et retourne les diagnostics avant de rendre la main au LLM.",
                nature=ToolNature.TEST,
                risk=RiskLevel.LOW,
                timeout=240,
                effects=("MAY_COMPILE", "MAY_DOMAIN_RELOAD"),
                completion_barrier=CompletionBarrier.COMPILE_SETTLED.value,
                idempotent=True,
            ),
            ToolDescriptor(
                tool_id="unity.observe",
                provider="unity-visual",
                description="Observe GAME, SCENE, EDITOR ou RUNTIME sans exposer le backend de capture au LLM.",
                nature=ToolNature.READ,
                risk=RiskLevel.LOW,
                timeout=60,
                completion_barrier=CompletionBarrier.EDITOR_QUIESCENT.value,
                idempotent=True,
            ),
        )

    def execute_tool(self, request: ToolRequest, project_root: Path) -> ToolResult:
        if request.profile_id != self.metadata.profile_id:
            raise ToolExecutionError(
                f"Requête destinée au profil {request.profile_id!r}, profil actif {self.metadata.profile_id!r}."
            )
        if read_unity_project(project_root) is None:
            raise ToolExecutionError("La racine sélectionnée n'est pas un projet Unity valide.")
        if request.tool_id == "unity.recompile":
            self._state = UnityProfileState.COMPILING
            result = self.compiler.compile(project_root, timeout=request.timeout or 240)
            self._state = result.state
            if result.outcome == CompileOutcome.SUCCESS:
                status = ExecutionStatus.SUCCESS
            elif result.outcome == CompileOutcome.TIMEOUT:
                status = ExecutionStatus.TIMEOUT
            else:
                status = ExecutionStatus.ERROR
            return ToolResult(
                request_id=request.request_id,
                profile_id=self.metadata.profile_id,
                provider=request.provider,
                tool_id=request.tool_id,
                status=status,
                profile_state=self.current_state(),
                data={
                    "outcome": result.outcome.value,
                    "barrier": result.barrier.value,
                    "compileErrors": list(result.errors),
                    "compileWarnings": list(result.warnings),
                },
                errors=result.errors,
                warnings=result.warnings,
                recommended_next=("TOOL", "EXECUTION") if result.success else ("TOOL",),
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        if request.tool_id == "unity.observe":
            raw_intent = str(request.arguments.get("intent", "GAME")).strip().upper()
            try:
                intent = VisualIntent(raw_intent)
            except ValueError as exc:
                raise ToolExecutionError(f"Intention visuelle Unity inconnue : {raw_intent}") from exc
            try:
                evidence = self.visual_router.capture(intent, project_root)
            except VisualCaptureError as exc:
                return ToolResult(
                    request_id=request.request_id,
                    profile_id=self.metadata.profile_id,
                    provider=request.provider,
                    tool_id=request.tool_id,
                    status=ExecutionStatus.ERROR,
                    profile_state=self.current_state(),
                    errors=(str(exc),),
                    recommended_next=("TOOL",),
                )
            return ToolResult(
                request_id=request.request_id,
                profile_id=self.metadata.profile_id,
                provider=request.provider,
                tool_id=request.tool_id,
                status=ExecutionStatus.SUCCESS,
                profile_state=self.current_state(),
                data={
                    "intent": evidence.intent.value,
                    "source": evidence.source,
                    "confidence": evidence.confidence.value,
                    "fallbackUsed": evidence.fallback_used,
                    "occlusionSafe": evidence.occlusion_safe,
                    "frameStable": evidence.frame_stable,
                    "semantic": evidence.semantic_data,
                },
                warnings=evidence.warnings,
                artifacts=tuple(str(path) for path in evidence.artifacts),
                recommended_next=("TOOL", "EXECUTION"),
            )
        raise ToolExecutionError(f"Outil Unity non supporté : {request.tool_id}")

    def build_initial_prompt(
        self,
        project_root: Path,
        goal: str,
        *,
        shell: str,
        os_name: str,
    ) -> str:
        core = build_generic_prompt(
            project_root,
            goal,
            shell=shell,
            os_name=os_name,
        )
        return core + build_unity_prompt_suffix()
