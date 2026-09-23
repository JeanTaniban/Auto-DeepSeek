from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import cv2

from ...visual_match import bgra_bytes_to_bgr
from ...win32_input import Win32DesktopInput, bgra_is_likely_black
from .visual import EvidenceBundle, VisualConfidence, VisualIntent


class UnityEditorWindowVisualProvider:
    """Windows fallback that captures the visible Unity Editor client.

    This provider is deliberately limited to EDITOR. GAME/SCENE should use
    native Unity/Pipeline/MCP providers when implemented; RUNTIME should use
    the built Player + TargetSession path.
    """

    provider_id = "unity-editor-window"
    priority = 10

    def __init__(self, desktop: Win32DesktopInput, *, artifact_root: Path | None = None) -> None:
        self.desktop = desktop
        self.artifact_root = artifact_root or (
            Path(tempfile.gettempdir()) / "AutoDeepSeek" / "unity-visual"
        )

    def supports(self, intent: VisualIntent) -> bool:
        return intent == VisualIntent.EDITOR and self.desktop.available

    def _find_editor_window(self, project_root: Path):
        project_name = project_root.resolve().name.casefold()
        candidates = []
        for info in self.desktop.enumerate_windows():
            title = info.title.casefold()
            if "unity" not in title:
                continue
            # Never guess across multiple projects: the project name must be
            # visible in the top-level Editor title for this fallback.
            if project_name and project_name not in title:
                continue
            candidates.append(info)
        if not candidates:
            raise RuntimeError(
                f"Aucune fenêtre Unity Editor visible correspondant au projet {project_root.name!r}."
            )
        return max(candidates, key=lambda item: item.client_rect.width * item.client_rect.height)

    def capture(self, intent: VisualIntent, project_root: Path) -> EvidenceBundle:
        if intent != VisualIntent.EDITOR:
            raise RuntimeError(f"Provider Windows Editor incompatible avec {intent.value}.")
        info = self._find_editor_window(project_root)
        # capture_window_client raises/activates the Editor before sampling and
        # already contains the PrintWindow fallback for suspicious black frames.
        frame = self.desktop.capture_window_client(info.hwnd)
        if bgra_is_likely_black(frame.pixels):
            raise RuntimeError("Capture Unity Editor noire ou quasi noire.")

        image = bgra_bytes_to_bgr(frame.pixels, frame.width, frame.height)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        filename = f"unity-editor-{int(time.time() * 1000)}.png"
        path = self.artifact_root / filename
        if not cv2.imwrite(str(path), image):
            raise RuntimeError("Impossible d'écrire l'artifact PNG Unity Editor.")

        return EvidenceBundle(
            intent=intent,
            source="UNITY_EDITOR_WINDOW",
            confidence=VisualConfidence.MEDIUM,
            artifacts=(path,),
            semantic_data={
                "windowTitle": info.title,
                "hwnd": info.hwnd,
                "clientWidth": frame.width,
                "clientHeight": frame.height,
            },
            warnings=(
                "Capture OS de l'Editor : utile pour Hierarchy/Inspector/dialogues, "
                "mais moins forte qu'une capture native Game/Scene View.",
            ),
            fallback_used=True,
            occlusion_safe=False,
            frame_stable=False,
        )
