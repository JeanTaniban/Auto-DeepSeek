from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from .profiles import ProfileManager
from .profiles.tools import ToolRequest, ToolResult
from .redaction import redact_secrets


class ProfileToolRunner:
    """Run one profile tool off the Tk thread."""

    def __init__(self, manager: ProfileManager) -> None:
        self.manager = manager
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(
        self,
        request: ToolRequest,
        project_root: Path,
        *,
        on_done: Callable[[ToolResult | Exception], None],
    ) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("Un outil de profil est déjà en cours.")

            def worker() -> None:
                try:
                    result = self.manager.execute_tool(request, project_root)
                except Exception as exc:
                    on_done(exc)
                else:
                    on_done(result)

            self._thread = threading.Thread(
                target=worker,
                name=f"profile-tool-{request.tool_id}",
                daemon=True,
            )
            self._thread.start()


def format_tool_result(result: ToolResult, *, max_output_chars: int = 12000) -> str:
    limit = max(1000, int(max_output_chars))

    def bounded(value: str) -> str:
        safe = redact_secrets(value or "")
        if len(safe) <= limit:
            return safe
        return safe[:limit] + "\n...[truncated]"

    lines = [
        "#RelayResult",
        "Protocol: 2",
        "Kind: TOOL",
        f"ID: {result.request_id}",
        f"Status: {result.status.value}",
        f"Profile: {result.profile_id}",
        f"Provider: {result.provider}",
        f"Tool: {result.tool_id}",
        f"ProfileState: {result.profile_state.value}",
    ]
    if result.recommended_next:
        lines.append("RecommendedNext: " + ",".join(result.recommended_next))
    if result.exit_code is not None:
        lines.append(f"ExitCode: {result.exit_code}")
    if result.artifacts:
        lines.append("Artifacts: " + " | ".join(result.artifacts))
    if result.warnings:
        lines.append("Warnings: " + " | ".join(result.warnings))
    if result.errors:
        lines.append("Errors: " + " | ".join(result.errors))
    if result.data:
        lines.extend(("", "DATA:", json.dumps(result.data, ensure_ascii=False, indent=2, sort_keys=True)))
    if result.stdout:
        lines.extend(("", "STDOUT:", bounded(result.stdout)))
    if result.stderr:
        lines.extend(("", "STDERR:", bounded(result.stderr)))
    return "\n".join(lines).strip() + "\n"
