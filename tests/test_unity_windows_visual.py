from pathlib import Path

from clipboard_agent.profiles import build_default_profile_registry
from clipboard_agent.profiles.unity.visual import VisualConfidence, VisualIntent
from clipboard_agent.profiles.unity.windows_visual import UnityEditorWindowVisualProvider
from clipboard_agent.win32_input import ScreenFrame, ScreenRect, WindowInfo


class FakeDesktop:
    available = True

    def __init__(self):
        self.captured = []
        rect = ScreenRect(10, 20, 810, 620)
        self.windows = [
            WindowInfo(11, 101, "OtherProject - Main - Unity", rect, rect),
            WindowInfo(22, 202, "MyGame - SampleScene - Unity 6", rect, rect),
        ]

    def enumerate_windows(self):
        return list(self.windows)

    def capture_window_client(self, hwnd):
        self.captured.append(hwnd)
        # Visible blue BGRA frame, 4x4.
        pixel = bytes((255, 0, 0, 255))
        return ScreenFrame(4, 4, pixel * 16)


def test_windows_visual_provider_selects_matching_unity_project_and_writes_png(tmp_path):
    project = tmp_path / "MyGame"
    project.mkdir()
    artifacts = tmp_path / "artifacts"
    desktop = FakeDesktop()
    provider = UnityEditorWindowVisualProvider(desktop, artifact_root=artifacts)

    evidence = provider.capture(VisualIntent.EDITOR, project)

    assert desktop.captured == [22]
    assert evidence.source == "UNITY_EDITOR_WINDOW"
    assert evidence.confidence == VisualConfidence.MEDIUM
    assert evidence.fallback_used is True
    assert evidence.occlusion_safe is False
    assert len(evidence.artifacts) == 1
    assert evidence.artifacts[0].is_file()
    assert evidence.semantic_data["windowTitle"].startswith("MyGame")


def test_windows_visual_provider_does_not_guess_another_unity_project(tmp_path):
    project = tmp_path / "MissingProject"
    project.mkdir()
    provider = UnityEditorWindowVisualProvider(FakeDesktop(), artifact_root=tmp_path / "artifacts")

    try:
        provider.capture(VisualIntent.EDITOR, project)
    except RuntimeError as exc:
        assert "Aucune fenêtre Unity Editor visible" in str(exc)
    else:
        raise AssertionError("The provider must not capture a different Unity project")


def test_default_registry_wires_editor_visual_fallback_when_desktop_is_available():
    desktop = FakeDesktop()
    registry = build_default_profile_registry(desktop=desktop)
    unity = registry.get("unity")
    providers = unity.visual_router.providers
    assert len(providers) == 1
    assert providers[0].provider_id == "unity-editor-window"
    assert providers[0].supports(VisualIntent.EDITOR) is True
    assert providers[0].supports(VisualIntent.GAME) is False
