from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AutoState(str, Enum):
    """Internal Agent Auto states.

    The UI status text is intentionally separate: these values describe the
    control-flow contract used to decide which asynchronous event is legal.
    """

    OFF = "OFF"
    STARTING = "STARTING"
    SYNCING_EXISTING_REPLY = "SYNCING_EXISTING_REPLY"
    WAITING_INITIAL_CLIPBOARD = "WAITING_INITIAL_CLIPBOARD"
    PROCESSING_INITIAL_REPLY = "PROCESSING_INITIAL_REPLY"
    RECOVERING_LAST_RESULT = "RECOVERING_LAST_RESULT"
    RECOVERING_SYSTEM_ERROR = "RECOVERING_SYSTEM_ERROR"
    WAITING_CLIPBOARD = "WAITING_CLIPBOARD"
    PROCESSING_REPLY = "PROCESSING_REPLY"
    EXECUTING = "EXECUTING"
    PROFILE_TOOL_RUNNING = "PROFILE_TOOL_RUNNING"
    SENDING = "SENDING"
    WAITING_VISUAL = "WAITING_VISUAL"
    TARGET_STARTING = "TARGET_STARTING"
    TARGET_RUNNING = "TARGET_RUNNING"
    TARGET_RESTORING = "TARGET_RESTORING"
    TEST_OPENING = "TEST_OPENING"
    TEST_ACTING = "TEST_ACTING"
    TEST_RESTORING = "TEST_RESTORING"
    TEST_CLOSING = "TEST_CLOSING"
    PAUSED = "PAUSED"


class AutoTransitionError(RuntimeError):
    pass


_PROCESSING_TARGETS = frozenset({
    AutoState.EXECUTING,
    AutoState.PROFILE_TOOL_RUNNING,
    AutoState.TARGET_STARTING,
    AutoState.TEST_OPENING,
    AutoState.TEST_ACTING,
    AutoState.TEST_CLOSING,
    AutoState.PAUSED,
    AutoState.OFF,
})

_ALLOWED: dict[AutoState, frozenset[AutoState]] = {
    AutoState.OFF: frozenset({AutoState.STARTING}),
    AutoState.STARTING: frozenset({AutoState.SYNCING_EXISTING_REPLY, AutoState.PAUSED, AutoState.OFF}),
    AutoState.SYNCING_EXISTING_REPLY: frozenset({AutoState.WAITING_INITIAL_CLIPBOARD, AutoState.PAUSED, AutoState.OFF}),
    AutoState.WAITING_INITIAL_CLIPBOARD: frozenset({AutoState.PROCESSING_INITIAL_REPLY, AutoState.PAUSED, AutoState.OFF}),
    AutoState.PROCESSING_INITIAL_REPLY: frozenset((*_PROCESSING_TARGETS, AutoState.RECOVERING_LAST_RESULT)),
    AutoState.RECOVERING_LAST_RESULT: frozenset({AutoState.SENDING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.RECOVERING_SYSTEM_ERROR: frozenset({AutoState.SENDING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.WAITING_CLIPBOARD: frozenset({AutoState.PROCESSING_REPLY, AutoState.PAUSED, AutoState.OFF}),
    AutoState.PROCESSING_REPLY: _PROCESSING_TARGETS,
    AutoState.EXECUTING: frozenset({AutoState.SENDING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.PROFILE_TOOL_RUNNING: frozenset({AutoState.SENDING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.SENDING: frozenset({AutoState.WAITING_VISUAL, AutoState.PAUSED, AutoState.OFF}),
    AutoState.WAITING_VISUAL: frozenset({AutoState.WAITING_CLIPBOARD, AutoState.PAUSED, AutoState.OFF}),
    AutoState.TARGET_STARTING: frozenset({AutoState.TARGET_RUNNING, AutoState.TARGET_RESTORING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.TARGET_RUNNING: frozenset({AutoState.TARGET_RESTORING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.TARGET_RESTORING: frozenset({AutoState.SENDING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.TEST_OPENING: frozenset({AutoState.TEST_RESTORING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.TEST_ACTING: frozenset({AutoState.TEST_RESTORING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.TEST_RESTORING: frozenset({AutoState.SENDING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.TEST_CLOSING: frozenset({AutoState.SENDING, AutoState.PAUSED, AutoState.OFF}),
    AutoState.PAUSED: frozenset({AutoState.OFF}),
}

# Auto repair self is a control-plane escape hatch, not a normal work state.
# Active states may report a recoverable system fault except when the browser
# output itself is mid-send: SENDING can already contain a partial paste, so a
# second message would be unsafe. OFF/PAUSED also never resume implicitly.
for _source in tuple(_ALLOWED):
    if _source not in {
        AutoState.OFF,
        AutoState.PAUSED,
        AutoState.SENDING,
        AutoState.RECOVERING_SYSTEM_ERROR,
    }:
        _ALLOWED[_source] = frozenset((*_ALLOWED[_source], AutoState.RECOVERING_SYSTEM_ERROR))


@dataclass(slots=True)
class AutoStateMachine:
    state: AutoState = AutoState.OFF

    def transition(self, target: AutoState) -> AutoState:
        target = AutoState(target)
        if target == self.state:
            return self.state
        if target not in _ALLOWED[self.state]:
            raise AutoTransitionError(f"Transition Agent Auto interdite : {self.state.value} -> {target.value}")
        self.state = target
        return self.state

    def force_off(self) -> AutoState:
        """Emergency/idempotent stop used by fail-safe paths."""
        self.state = AutoState.OFF
        return self.state

    @staticmethod
    def allowed_from(state: AutoState) -> frozenset[AutoState]:
        return _ALLOWED[AutoState(state)]
