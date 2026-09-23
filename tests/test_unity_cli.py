import subprocess
from pathlib import Path

from clipboard_agent.profiles.unity.cli import UnityCliRunner


def test_unity_cli_recompile_uses_machine_readable_noninteractive_contract(tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"success":true,"data":{"compileErrors":[]}}',
            stderr="",
        )

    runner = UnityCliRunner(executable="unity", run_process=fake_run)
    result = runner.recompile(tmp_path, timeout=321)

    assert calls == [(
        [
            "unity",
            "--format", "json",
            "--non-interactive",
            "--no-banner",
            "--no-color",
            "recompile",
            "--project-path", str(tmp_path),
        ],
        {
            "capture_output": True,
            "text": True,
            "timeout": 321,
            "check": False,
        },
    )]
    assert result.success is True
    assert result.data == {"success": True, "data": {"compileErrors": []}}


def test_unity_cli_json_envelope_can_override_zero_exit_success():
    def fake_run(command, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"success":false,"errors":[{"message":"Editor unavailable"}]}',
            stderr="",
        )

    result = UnityCliRunner(run_process=fake_run).run(["status", "--project-path", "."])
    assert result.exit_code == 0
    assert result.success is False


def test_unity_cli_timeout_is_structured_instead_of_raising(tmp_path):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    result = UnityCliRunner(run_process=fake_run).recompile(tmp_path, timeout=4)
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.success is False
