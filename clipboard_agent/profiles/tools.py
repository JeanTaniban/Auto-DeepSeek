from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..models import ExecutionStatus, RiskLevel
from .models import ProfileState


class ToolNature(str, Enum):
    READ = "READ"
    MODIFY = "MODIFY"
    TEST = "TEST"
    BUILD = "BUILD"
    RUNTIME = "RUNTIME"
    INSTALL = "INSTALL"


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    tool_id: str
    provider: str
    description: str
    nature: ToolNature = ToolNature.READ
    risk: RiskLevel = RiskLevel.LOW
    allowed_states: tuple[ProfileState, ...] = (ProfileState.READY,)
    timeout: int = 120
    effects: tuple[str, ...] = ()
    completion_barrier: str = "NONE"
    idempotent: bool = True


@dataclass(frozen=True, slots=True)
class ToolRequest:
    request_id: str
    profile_id: str
    provider: str
    tool_id: str
    arguments: dict[str, object] = field(default_factory=dict)
    timeout: int | None = None


@dataclass(slots=True)
class ToolResult:
    request_id: str
    profile_id: str
    provider: str
    tool_id: str
    status: ExecutionStatus
    profile_state: ProfileState
    data: dict[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    recommended_next: tuple[str, ...] = ()
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""


class ToolExecutionError(RuntimeError):
    pass
