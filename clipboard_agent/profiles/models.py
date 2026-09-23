from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class HealthStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"


class ProfileState(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    READY = "READY"
    BUSY = "BUSY"
    USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class ProfileMetadata:
    profile_id: str
    display_name: str
    version: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class DetectionResult:
    matched: bool
    confidence: float = 0.0
    reasons: tuple[str, ...] = ()
    project_type: str = ""
    detected_version: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HealthCheck:
    check_id: str
    status: HealthStatus
    message: str
    details: dict[str, object] = field(default_factory=dict)
    remediation: str = ""
    auto_remediation_available: bool = False
    risk: str = "LOW"


@dataclass(frozen=True, slots=True)
class HealthReport:
    checks: tuple[HealthCheck, ...] = ()

    @property
    def overall(self) -> HealthStatus:
        if not self.checks:
            return HealthStatus.PASS
        statuses = {check.status for check in self.checks}
        if HealthStatus.FAIL in statuses:
            return HealthStatus.FAIL
        if HealthStatus.USER_ACTION_REQUIRED in statuses:
            return HealthStatus.USER_ACTION_REQUIRED
        if HealthStatus.WARN in statuses:
            return HealthStatus.WARN
        return HealthStatus.PASS
