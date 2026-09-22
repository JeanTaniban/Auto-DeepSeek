from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .models import ExecutionRequest, ExecutionResult, ExecutionStatus


OutputCallback = Callable[[str, str], None]
DoneCallback = Callable[[ExecutionResult], None]


class ExecutionManager:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._cancel_requested = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
            proc = self._process
        if proc and proc.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def execute_async(
        self,
        request: ExecutionRequest,
        cwd: Path,
        on_output: OutputCallback,
        on_done: DoneCallback,
    ) -> None:
        if self.running:
            raise RuntimeError("Une commande est déjà en cours d'exécution.")
        self._cancel_requested = False
        thread = threading.Thread(
            target=self._worker,
            args=(request, cwd, on_output, on_done),
            daemon=True,
        )
        thread.start()

    @staticmethod
    def _child_environment() -> dict[str, str]:
        """Keep agent subprocesses on the same Python runtime as the Relay."""
        env = os.environ.copy()
        python_exe = Path(sys.executable).resolve()
        preferred = [str(python_exe.parent)]
        scripts = python_exe.parent / "Scripts"
        if scripts.exists():
            preferred.append(str(scripts))
        current_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([*preferred, current_path]) if current_path else os.pathsep.join(preferred)
        env["CAR_PYTHON_EXE"] = str(python_exe)
        return env

    def _build_invocation(self, request: ExecutionRequest, *, interactive: bool = False) -> list[str]:
        shell = request.shell.lower()
        if os.name == "nt":
            if shell in {"powershell", "pwsh", "ps"}:
                exe = "powershell.exe" if shell != "pwsh" else "pwsh.exe"
                flags = [exe, "-NoLogo", "-NoProfile"]
                if interactive:
                    flags.append("-NoExit")
                    command = request.command
                else:
                    flags.append("-NonInteractive")
                    command = (
                        f"& {{ {request.command} }}; "
                        "$carSuccess = $?; $carExitCode = $LASTEXITCODE; "
                        "if ($null -ne $carExitCode) { exit $carExitCode } "
                        "elseif (-not $carSuccess) { exit 1 } else { exit 0 }"
                    )
                return [*flags, "-Command", command]
            if shell in {"cmd", "cmd.exe"}:
                return ["cmd.exe", "/d", "/s", "/k" if interactive else "/c", request.command]
            return [shell, "-c", request.command]
        if shell in {"powershell", "pwsh", "ps"}:
            flags = ["pwsh", "-NoLogo", "-NoProfile"]
            if interactive:
                flags.append("-NoExit")
                command = request.command
            else:
                flags.append("-NonInteractive")
                command = (
                    f"& {{ {request.command} }}; "
                    "$carSuccess = $?; $carExitCode = $LASTEXITCODE; "
                    "if ($null -ne $carExitCode) { exit $carExitCode } "
                    "elseif (-not $carSuccess) { exit 1 } else { exit 0 }"
                )
            return [*flags, "-Command", command]
        if shell in {"bash", "sh", "zsh"}:
            return [shell, "-lc", request.command]
        return [shell, "-c", request.command]

    def launch_target_captured(self, request: ExecutionRequest, cwd: Path) -> subprocess.Popen[str]:
        """Launch a persistent Target App while capturing stdout/stderr.

        Unlike legacy ``#Multiple`` this process is kept alive across several
        LLM turns.  A non-interactive shell wrapper is intentional: direct GUI
        commands (``python app.py``, ``npm run dev``...) keep the wrapper alive
        while their descendants run, and their stdout/stderr remain observable
        for readiness checkpoints and debugging.
        """
        if os.name != "nt":
            raise RuntimeError("Les TestSessions persistantes sont disponibles uniquement sous Windows.")
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(
            self._build_invocation(request, interactive=False),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            env=self._child_environment(),
        )

    def launch_target(self, request: ExecutionRequest, cwd: Path) -> subprocess.Popen:
        """Launch a temporary interactive Target App process for #Multiple.

        Windows receives a visible console when the launch command is CLI. GUI
        children can still create their own windows; the Target App controller
        discovers the best top-level window in the full descendant process tree.
        """
        if os.name != "nt":
            raise RuntimeError("Les sessions Target App #Multiple sont disponibles uniquement sous Windows.")
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(
            self._build_invocation(request, interactive=True),
            cwd=str(cwd),
            creationflags=creationflags,
            env=self._child_environment(),
        )

    def launch_external(self, request: ExecutionRequest, cwd: Path) -> int:
        """Launch a visible external process for #Show and return its PID.

        On Windows a new console is created and kept open for PowerShell/cmd
        so CLI demos remain visible and interactive.
        """
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            proc = subprocess.Popen(
                self._build_invocation(request, interactive=True),
                cwd=str(cwd),
                creationflags=creationflags,
                env=self._child_environment(),
            )
            return int(proc.pid)

        # Best-effort terminal launch for development/testing on Unix. The V2
        # Agent Auto target is Windows; manual #Show remains useful elsewhere.
        import shutil

        invocation = self._build_invocation(request, interactive=True)
        candidates = [
            ("x-terminal-emulator", ["x-terminal-emulator", "-e", *invocation]),
            ("gnome-terminal", ["gnome-terminal", "--", *invocation]),
            ("konsole", ["konsole", "-e", *invocation]),
            ("xterm", ["xterm", "-e", *invocation]),
        ]
        for executable, command in candidates:
            if shutil.which(executable):
                proc = subprocess.Popen(command, cwd=str(cwd), env=self._child_environment())
                return int(proc.pid)
        raise RuntimeError("Aucun terminal externe compatible n'a été trouvé.")

    def _worker(self, request: ExecutionRequest, cwd: Path, on_output: OutputCallback, on_done: DoneCallback) -> None:
        started = time.monotonic()
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        creationflags = 0
        popen_kwargs: dict = {}
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(
                self._build_invocation(request),
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                env=self._child_environment(),
                **popen_kwargs,
            )
        except Exception as exc:
            on_done(
                ExecutionResult(
                    request_id=request.request_id,
                    status=ExecutionStatus.ERROR,
                    exit_code=None,
                    duration=time.monotonic() - started,
                    cwd=cwd,
                    stdout="",
                    stderr=str(exc),
                    command=request.command,
                    note="Impossible de démarrer le processus.",
                )
            )
            return

        with self._lock:
            self._process = proc

        def pump(stream, channel: str, sink: list[str]) -> None:
            try:
                for line in iter(stream.readline, ""):
                    sink.append(line)
                    on_output(channel, line)
            finally:
                stream.close()

        out_thread = threading.Thread(target=pump, args=(proc.stdout, "stdout", stdout_parts), daemon=True)
        err_thread = threading.Thread(target=pump, args=(proc.stderr, "stderr", stderr_parts), daemon=True)
        out_thread.start()
        err_thread.start()

        timed_out = False
        try:
            proc.wait(timeout=request.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.cancel()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        out_thread.join(timeout=2)
        err_thread.join(timeout=2)

        with self._lock:
            cancelled = self._cancel_requested and not timed_out
            self._process = None

        if timed_out:
            status = ExecutionStatus.TIMEOUT
        elif cancelled:
            status = ExecutionStatus.CANCELLED
        elif proc.returncode == 0:
            status = ExecutionStatus.SUCCESS
        else:
            status = ExecutionStatus.ERROR

        on_done(
            ExecutionResult(
                request_id=request.request_id,
                status=status,
                exit_code=proc.returncode,
                duration=time.monotonic() - started,
                cwd=cwd,
                stdout="".join(stdout_parts),
                stderr="".join(stderr_parts),
                command=request.command,
            )
        )
