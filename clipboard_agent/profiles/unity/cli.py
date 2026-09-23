from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True, slots=True)
class UnityCliResult:
    args: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    data: object | None = None
    success: bool = False
    timed_out: bool = False


class UnityCliRunner:
    """Small, injectable boundary around the experimental Unity CLI."""

    def __init__(
        self,
        executable: str = "unity",
        *,
        run_process: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.executable = executable
        self._run_process = run_process

    def available(self) -> bool:
        if Path(self.executable).is_file():
            return True
        return shutil.which(self.executable) is not None

    def run(self, args: Sequence[str], *, timeout: int = 120) -> UnityCliResult:
        command = [
            self.executable,
            *args,
            "--format",
            "json",
            "--non-interactive",
            "--no-banner",
            "--no-color",
        ]
        try:
            completed = self._run_process(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return UnityCliResult(
                args=tuple(command),
                exit_code=None,
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
                timed_out=True,
            )

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        parsed: object | None = None
        success = completed.returncode == 0
        if stdout.strip():
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and isinstance(parsed.get("success"), bool):
                success = bool(parsed["success"]) and completed.returncode == 0

        return UnityCliResult(
            args=tuple(command),
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            data=parsed,
            success=success,
        )

    def version(self, *, timeout: int = 15) -> UnityCliResult:
        return self.run(["--version"], timeout=timeout)

    def status(self, project_root: Path, *, timeout: int = 30) -> UnityCliResult:
        return self.run(["status", "--project-path", str(project_root)], timeout=timeout)

    def recompile(self, project_root: Path, *, timeout: int = 240) -> UnityCliResult:
        return self.run(["recompile", "--project-path", str(project_root)], timeout=timeout)
