from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .cli import UnityCliResult, UnityCliRunner
from .state import CompletionBarrier, UnityProfileState


class CompileOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    COMPILE_ERROR = "COMPILE_ERROR"
    INFRA_ERROR = "INFRA_ERROR"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True, slots=True)
class UnityCompileResult:
    outcome: CompileOutcome
    state: UnityProfileState
    barrier: CompletionBarrier
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""

    @property
    def success(self) -> bool:
        return self.outcome == CompileOutcome.SUCCESS


def _string_messages(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, list):
        messages: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                messages.append(item)
            elif isinstance(item, dict):
                message = item.get("message") or item.get("text") or item.get("error")
                if isinstance(message, str) and message.strip():
                    messages.append(message)
        return tuple(messages)
    if isinstance(value, dict):
        message = value.get("message") or value.get("text") or value.get("error")
        if isinstance(message, str) and message.strip():
            return (message,)
    return ()


def _diagnostics(result: UnityCliResult) -> tuple[tuple[str, ...], tuple[str, ...]]:
    errors: list[str] = []
    warnings: list[str] = []
    data = result.data
    if isinstance(data, dict):
        errors.extend(_string_messages(data.get("errors")))
        warnings.extend(_string_messages(data.get("warnings")))
        nested = data.get("data")
        if isinstance(nested, dict):
            errors.extend(_string_messages(nested.get("errors")))
            errors.extend(_string_messages(nested.get("compileErrors")))
            warnings.extend(_string_messages(nested.get("warnings")))
            warnings.extend(_string_messages(nested.get("compileWarnings")))
    if not errors and result.exit_code not in {0, None} and result.stderr.strip():
        errors.append(result.stderr.strip())
    return tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(warnings))


class UnityCompileCoordinator:
    """Treat C# compilation as a domain transaction, not an opaque shell call."""

    def __init__(self, cli: UnityCliRunner) -> None:
        self.cli = cli

    def compile(self, project_root: Path, *, timeout: int = 240) -> UnityCompileResult:
        raw = self.cli.recompile(project_root, timeout=timeout)
        errors, warnings = _diagnostics(raw)
        if raw.timed_out:
            return UnityCompileResult(
                outcome=CompileOutcome.TIMEOUT,
                state=UnityProfileState.ERROR,
                barrier=CompletionBarrier.COMPILE_SETTLED,
                errors=errors or ("Unity recompile a dépassé le timeout.",),
                warnings=warnings,
                exit_code=raw.exit_code,
                stdout=raw.stdout,
                stderr=raw.stderr,
            )
        if raw.success and not errors:
            return UnityCompileResult(
                outcome=CompileOutcome.SUCCESS,
                state=UnityProfileState.EDITOR_READY,
                barrier=CompletionBarrier.COMPILE_SETTLED,
                warnings=warnings,
                exit_code=raw.exit_code,
                stdout=raw.stdout,
                stderr=raw.stderr,
            )
        if errors:
            return UnityCompileResult(
                outcome=CompileOutcome.COMPILE_ERROR,
                state=UnityProfileState.EDITOR_READY,
                barrier=CompletionBarrier.COMPILE_SETTLED,
                errors=errors,
                warnings=warnings,
                exit_code=raw.exit_code,
                stdout=raw.stdout,
                stderr=raw.stderr,
            )
        return UnityCompileResult(
            outcome=CompileOutcome.INFRA_ERROR,
            state=UnityProfileState.ERROR,
            barrier=CompletionBarrier.COMPILE_SETTLED,
            errors=("Unity CLI n'a pas fourni de verdict de compilation exploitable.",),
            warnings=warnings,
            exit_code=raw.exit_code,
            stdout=raw.stdout,
            stderr=raw.stderr,
        )
