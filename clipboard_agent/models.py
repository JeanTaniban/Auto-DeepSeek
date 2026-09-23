from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class RiskLevel(str, Enum):
    LOW = "LOW"
    MODIFY = "MODIFY"
    SENSITIVE = "SENSITIVE"
    BLOCKED = "BLOCKED"


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class DirectiveKind(str, Enum):
    EXECUTION = "EXECUTION"
    SHOW = "SHOW"
    MULTIPLE = "MULTIPLE"
    OPEN_TEST_SESSION = "OPEN_TEST_SESSION"
    CLOSE_TEST_SESSION = "CLOSE_TEST_SESSION"
    TEST_ACTIONS = "TEST_ACTIONS"
    TOOL = "TOOL"
    END = "END"


class InteractionKind(str, Enum):
    CLICK = "CLICK"
    TYPE_INPUT = "TYPE_INPUT"
    KEY = "KEY"
    WAIT = "WAIT"
    OBSERVE = "OBSERVE"


@dataclass(frozen=True, slots=True)
class InteractionAction:
    kind: InteractionKind
    x: int | None = None
    y: int | None = None
    text: str = ""
    key: str = ""
    wait_ms: int = 0
    label: str = ""


@dataclass(slots=True)
class ExecutionRequest:
    command: str
    shell: str = "powershell"
    cwd: str = "."
    timeout: int = 120
    request_id: str = ""
    raw_text: str = ""


@dataclass(slots=True)
class AgentDirective:
    kind: DirectiveKind
    request: ExecutionRequest | None = None
    actions: tuple[InteractionAction, ...] = ()
    raw_text: str = ""
    summary: str = ""
    request_id: str = ""
    ready: str = "auto"
    tool_profile: str = ""
    tool_provider: str = ""
    tool_id: str = ""
    tool_arguments: dict[str, object] = field(default_factory=dict)
    tool_timeout: int | None = None


@dataclass(slots=True)
class SecurityDecision:
    risk: RiskLevel
    reason: str
    requires_confirmation: bool


@dataclass(slots=True)
class ExecutionResult:
    request_id: str
    status: ExecutionStatus
    exit_code: Optional[int]
    duration: float
    cwd: Path
    stdout: str
    stderr: str
    command: str
    note: str = ""
