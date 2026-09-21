import os
import threading
from pathlib import Path

from clipboard_agent.execution import ExecutionManager
from clipboard_agent.models import ExecutionRequest, ExecutionStatus


def wait_run(req: ExecutionRequest, cwd: Path):
    manager = ExecutionManager()
    done = threading.Event()
    holder = {}
    manager.execute_async(req, cwd, lambda *_: None, lambda result: (holder.setdefault("result", result), done.set()))
    assert done.wait(10)
    return holder["result"]


def test_execution_success(tmp_path: Path):
    req = ExecutionRequest(command="python -c \"print('ok')\"", shell="powershell" if os.name == "nt" else "bash", timeout=5, request_id="t1")
    result = wait_run(req, tmp_path)
    assert result.status == ExecutionStatus.SUCCESS
    assert "ok" in result.stdout


def test_execution_error(tmp_path: Path):
    req = ExecutionRequest(command="python -c \"import sys; sys.exit(7)\"", shell="powershell" if os.name == "nt" else "bash", timeout=5, request_id="t2")
    result = wait_run(req, tmp_path)
    assert result.status == ExecutionStatus.ERROR
    assert result.exit_code == 7


def test_execution_timeout(tmp_path: Path):
    req = ExecutionRequest(command="python -c \"import time; time.sleep(2)\"", shell="powershell" if os.name == "nt" else "bash", timeout=1, request_id="t3")
    result = wait_run(req, tmp_path)
    assert result.status == ExecutionStatus.TIMEOUT


def test_noninteractive_powershell_propagates_native_exit_code():
    manager = ExecutionManager()
    req = ExecutionRequest(command="python -c \"import sys; sys.exit(7)\"", shell="powershell", timeout=5, request_id="exit")
    invocation = manager._build_invocation(req, interactive=False)
    command = invocation[invocation.index("-Command") + 1]
    assert "$carSuccess = $?" in command
    assert "$carExitCode = $LASTEXITCODE" in command
    assert "exit $carExitCode" in command


def test_interactive_invocation_keeps_powershell_open():
    manager = ExecutionManager()
    req = ExecutionRequest(command="Write-Host hello", shell="powershell", timeout=5, request_id="show")
    invocation = manager._build_invocation(req, interactive=True)
    assert "-NoExit" in invocation
    assert "-Command" in invocation
    command = invocation[invocation.index("-Command") + 1]
    assert command == "Write-Host hello"
    assert "$carExitCode" not in command
