from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UnityProfileState(str, Enum):
    """Internal Unity domain states.

    These states are intentionally more detailed than the generic ProfileState
    exposed to the LLM. They model the Unity/Editor lifecycle and are used to
    reject operations that would race compilation, import, Play Mode, tests or
    runtime execution.
    """

    UNINITIALIZED = "UNINITIALIZED"
    CHECKING = "CHECKING"
    READY = "READY"
    EDITOR_STARTING = "EDITOR_STARTING"
    EDITOR_READY = "EDITOR_READY"
    IMPORTING = "IMPORTING"
    COMPILING = "COMPILING"
    RELOADING = "RELOADING"
    PLAYMODE_ENTERING = "PLAYMODE_ENTERING"
    PLAY_MODE = "PLAY_MODE"
    PLAYMODE_EXITING = "PLAYMODE_EXITING"
    TESTING = "TESTING"
    BUILDING = "BUILDING"
    RUNTIME_STARTING = "RUNTIME_STARTING"
    RUNTIME_TESTING = "RUNTIME_TESTING"
    USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


class CompletionBarrier(str, Enum):
    NONE = "NONE"
    EDITOR_QUIESCENT = "EDITOR_QUIESCENT"
    IMPORT_SETTLED = "IMPORT_SETTLED"
    COMPILE_SETTLED = "COMPILE_SETTLED"
    RELOAD_SETTLED = "RELOAD_SETTLED"
    IMPORT_AND_COMPILE_SETTLED = "IMPORT_AND_COMPILE_SETTLED"
    PLAYMODE_ENTERED = "PLAYMODE_ENTERED"
    PLAYMODE_EXITED = "PLAYMODE_EXITED"
    TEST_VERDICT = "TEST_VERDICT"
    BUILD_FINISHED = "BUILD_FINISHED"
    RUNTIME_READY = "RUNTIME_READY"


class UnityStateTransitionError(RuntimeError):
    pass


_ALLOWED: dict[UnityProfileState, frozenset[UnityProfileState]] = {
    UnityProfileState.UNINITIALIZED: frozenset({
        UnityProfileState.CHECKING,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.CHECKING: frozenset({
        UnityProfileState.READY,
        UnityProfileState.EDITOR_READY,
        UnityProfileState.USER_ACTION_REQUIRED,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.READY: frozenset({
        UnityProfileState.CHECKING,
        UnityProfileState.EDITOR_STARTING,
        UnityProfileState.COMPILING,
        UnityProfileState.TESTING,
        UnityProfileState.BUILDING,
        UnityProfileState.RUNTIME_STARTING,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.EDITOR_STARTING: frozenset({
        UnityProfileState.EDITOR_READY,
        UnityProfileState.USER_ACTION_REQUIRED,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.EDITOR_READY: frozenset({
        UnityProfileState.CHECKING,
        UnityProfileState.IMPORTING,
        UnityProfileState.COMPILING,
        UnityProfileState.PLAYMODE_ENTERING,
        UnityProfileState.TESTING,
        UnityProfileState.BUILDING,
        UnityProfileState.RUNTIME_STARTING,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.IMPORTING: frozenset({
        UnityProfileState.COMPILING,
        UnityProfileState.RELOADING,
        UnityProfileState.EDITOR_READY,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.COMPILING: frozenset({
        UnityProfileState.RELOADING,
        UnityProfileState.EDITOR_READY,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.RELOADING: frozenset({
        UnityProfileState.EDITOR_READY,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.PLAYMODE_ENTERING: frozenset({
        UnityProfileState.PLAY_MODE,
        UnityProfileState.EDITOR_READY,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.PLAY_MODE: frozenset({
        UnityProfileState.PLAYMODE_EXITING,
        UnityProfileState.TESTING,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.PLAYMODE_EXITING: frozenset({
        UnityProfileState.RELOADING,
        UnityProfileState.EDITOR_READY,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.TESTING: frozenset({
        UnityProfileState.READY,
        UnityProfileState.EDITOR_READY,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.BUILDING: frozenset({
        UnityProfileState.READY,
        UnityProfileState.EDITOR_READY,
        UnityProfileState.RUNTIME_STARTING,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.RUNTIME_STARTING: frozenset({
        UnityProfileState.RUNTIME_TESTING,
        UnityProfileState.READY,
        UnityProfileState.EDITOR_READY,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.RUNTIME_TESTING: frozenset({
        UnityProfileState.READY,
        UnityProfileState.EDITOR_READY,
        UnityProfileState.DEGRADED,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.USER_ACTION_REQUIRED: frozenset({
        UnityProfileState.CHECKING,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.DEGRADED: frozenset({
        UnityProfileState.CHECKING,
        UnityProfileState.READY,
        UnityProfileState.EDITOR_READY,
        UnityProfileState.ERROR,
    }),
    UnityProfileState.ERROR: frozenset({
        UnityProfileState.CHECKING,
    }),
}


@dataclass(slots=True)
class UnityStateMachine:
    state: UnityProfileState = UnityProfileState.UNINITIALIZED

    def transition(self, target: UnityProfileState) -> UnityProfileState:
        target = UnityProfileState(target)
        if target == self.state:
            return self.state
        if target not in _ALLOWED[self.state]:
            raise UnityStateTransitionError(
                f"Transition Unity interdite : {self.state.value} -> {target.value}"
            )
        self.state = target
        return self.state

    def force_error(self) -> UnityProfileState:
        self.state = UnityProfileState.ERROR
        return self.state

    def reset(self) -> UnityProfileState:
        self.state = UnityProfileState.UNINITIALIZED
        return self.state

    @staticmethod
    def allowed_from(state: UnityProfileState) -> frozenset[UnityProfileState]:
        return _ALLOWED[UnityProfileState(state)]
