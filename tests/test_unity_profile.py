from pathlib import Path

from clipboard_agent.models import ExecutionStatus
from clipboard_agent.profiles import ProfileState, ToolRequest, UnityProfile
from clipboard_agent.profiles.unity.cli import UnityCliResult
from clipboard_agent.profiles.unity.compiler import CompileOutcome, UnityCompileCoordinator
from clipboard_agent.profiles.unity.visual import (
    EvidenceBundle,
    UnityVisualRouter,
    VisualConfidence,
    VisualIntent,
)


def _unity_project(tmp_path: Path, version: str = "6000.3.24f1") -> Path:
    (tmp_path / "Assets").mkdir()
    (tmp_path / "Packages").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ProjectSettings" / "ProjectVersion.txt").write_text(
        f"m_EditorVersion: {version}\n",
        encoding="utf-8",
    )
    return tmp_path


class FakeCli:
    def __init__(self, result: UnityCliResult | None = None, available: bool = True):
        self._result = result
        self._available = available
        self.calls = []

    def available(self):
        return self._available

    def recompile(self, project_root, *, timeout=240):
        self.calls.append((Path(project_root), timeout))
        assert self._result is not None
        return self._result


class FakeVisualProvider:
    def __init__(self, provider_id, priority, confidence, *, fail=False, source=None):
        self.provider_id = provider_id
        self.priority = priority
        self.confidence = confidence
        self.fail = fail
        self.source = source or provider_id

    def supports(self, intent):
        return intent == VisualIntent.GAME

    def capture(self, intent, project_root):
        del project_root
        if self.fail:
            raise RuntimeError("capture failed")
        return EvidenceBundle(
            intent=intent,
            source=self.source,
            confidence=self.confidence,
            artifacts=(Path("frame.png"),),
            occlusion_safe=self.confidence == VisualConfidence.HIGH,
            frame_stable=True,
        )


def test_unity_profile_detects_complete_project_and_version(tmp_path):
    root = _unity_project(tmp_path)
    profile = UnityProfile(cli=FakeCli())
    result = profile.detect_project(root)
    assert result.matched is True
    assert result.confidence == 0.99
    assert result.detected_version == "6000.3.24f1"


def test_unity_profile_avoids_assets_only_false_positive(tmp_path):
    (tmp_path / "Assets").mkdir()
    profile = UnityProfile(cli=FakeCli())
    assert profile.detect_project(tmp_path).matched is False


def test_unity_health_requires_cli_but_keeps_project_diagnostic(tmp_path):
    root = _unity_project(tmp_path)
    profile = UnityProfile(cli=FakeCli(available=False))
    report = profile.health_check(root)
    assert report.overall.value == "USER_ACTION_REQUIRED"
    assert [check.check_id for check in report.checks] == ["unity-project", "unity-cli"]
    assert profile.current_state() == ProfileState.USER_ACTION_REQUIRED


def test_unity_profile_exposes_compile_and_visual_tools(tmp_path):
    root = _unity_project(tmp_path)
    profile = UnityProfile(cli=FakeCli())
    descriptors = {item.tool_id: item for item in profile.tool_descriptors(root)}
    assert set(descriptors) == {"unity.recompile", "unity.observe"}
    assert descriptors["unity.recompile"].completion_barrier == "COMPILE_SETTLED"
    assert descriptors["unity.observe"].provider == "unity-visual"


def test_compile_coordinator_returns_success_after_structured_recompile(tmp_path):
    cli = FakeCli(UnityCliResult(
        args=("unity", "recompile"),
        exit_code=0,
        stdout='{"success":true,"data":{"warnings":[]}}',
        stderr="",
        data={"success": True, "data": {"warnings": []}},
        success=True,
    ))
    result = UnityCompileCoordinator(cli).compile(tmp_path, timeout=77)
    assert result.outcome == CompileOutcome.SUCCESS
    assert result.success is True
    assert result.barrier.value == "COMPILE_SETTLED"
    assert cli.calls == [(tmp_path, 77)]


def test_compile_coordinator_distinguishes_compile_error(tmp_path):
    cli = FakeCli(UnityCliResult(
        args=("unity", "recompile"),
        exit_code=6,
        stdout='{"success":false,"errors":[{"message":"CS0103 missing name"}]}',
        stderr="",
        data={"success": False, "errors": [{"message": "CS0103 missing name"}]},
        success=False,
    ))
    result = UnityCompileCoordinator(cli).compile(tmp_path)
    assert result.outcome == CompileOutcome.COMPILE_ERROR
    assert result.errors == ("CS0103 missing name",)
    assert result.state.value == "EDITOR_READY"


def test_visual_router_falls_back_and_marks_provenance(tmp_path):
    router = UnityVisualRouter((
        FakeVisualProvider("native", 100, VisualConfidence.HIGH, fail=True),
        FakeVisualProvider("mcp", 80, VisualConfidence.HIGH),
    ))
    evidence = router.capture(VisualIntent.GAME, tmp_path)
    assert evidence.source == "mcp"
    assert evidence.confidence == VisualConfidence.HIGH
    assert evidence.fallback_used is True
    assert evidence.usable is True


def test_unity_profile_executes_recompile_as_domain_tool(tmp_path):
    root = _unity_project(tmp_path)
    cli = FakeCli(UnityCliResult(
        args=("unity", "recompile"),
        exit_code=0,
        stdout='{"success":true}',
        stderr="",
        data={"success": True},
        success=True,
    ))
    profile = UnityProfile(cli=cli)
    result = profile.execute_tool(
        ToolRequest(
            request_id="compile-1",
            profile_id="unity",
            provider="unity-cli",
            tool_id="unity.recompile",
        ),
        root,
    )
    assert result.status == ExecutionStatus.SUCCESS
    assert result.data["outcome"] == "SUCCESS"
    assert result.data["barrier"] == "COMPILE_SETTLED"


def test_unity_profile_preserves_recompile_timeout_status(tmp_path):
    root = _unity_project(tmp_path)
    cli = FakeCli(UnityCliResult(
        args=("unity", "recompile"),
        exit_code=None,
        stdout="",
        stderr="",
        timed_out=True,
    ))
    profile = UnityProfile(cli=cli)
    result = profile.execute_tool(
        ToolRequest(
            request_id="compile-timeout",
            profile_id="unity",
            provider="unity-cli",
            tool_id="unity.recompile",
            timeout=12,
        ),
        root,
    )
    assert result.status == ExecutionStatus.TIMEOUT
    assert result.data["outcome"] == "TIMEOUT"
    assert cli.calls == [(root, 12)]


def test_unity_prompt_keeps_llm_visual_contract_simple(tmp_path):
    root = _unity_project(tmp_path)
    prompt = UnityProfile(cli=FakeCli()).build_initial_prompt(
        root,
        "Créer un FPS",
        shell="powershell",
        os_name="Windows",
    )
    assert "Profil actif : Unity" in prompt
    assert "`Action: TOOL` est une extension métier autorisée" in prompt
    assert "GAME" in prompt and "SCENE" in prompt and "EDITOR" in prompt and "RUNTIME" in prompt
    assert "ne choisis jamais toi-même MCP/Win32/backend de capture" in prompt
    assert "n'utilise pas de délai arbitraire" in prompt
