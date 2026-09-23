import pytest

from clipboard_agent.profiles.unity.state import (
    UnityProfileState,
    UnityStateMachine,
    UnityStateTransitionError,
)


def test_health_path_reaches_ready():
    sm = UnityStateMachine()
    assert sm.transition(UnityProfileState.CHECKING) == UnityProfileState.CHECKING
    assert sm.transition(UnityProfileState.READY) == UnityProfileState.READY


def test_recompile_path_from_ready_reaches_editor_ready():
    sm = UnityStateMachine(UnityProfileState.READY)
    assert sm.transition(UnityProfileState.COMPILING) == UnityProfileState.COMPILING
    assert sm.transition(UnityProfileState.EDITOR_READY) == UnityProfileState.EDITOR_READY


def test_recompile_can_model_domain_reload_before_ready():
    sm = UnityStateMachine(UnityProfileState.EDITOR_READY)
    sm.transition(UnityProfileState.COMPILING)
    assert sm.transition(UnityProfileState.RELOADING) == UnityProfileState.RELOADING
    assert sm.transition(UnityProfileState.EDITOR_READY) == UnityProfileState.EDITOR_READY


def test_playmode_cycle_is_explicit():
    sm = UnityStateMachine(UnityProfileState.EDITOR_READY)
    assert sm.transition(UnityProfileState.PLAYMODE_ENTERING) == UnityProfileState.PLAYMODE_ENTERING
    assert sm.transition(UnityProfileState.PLAY_MODE) == UnityProfileState.PLAY_MODE
    assert sm.transition(UnityProfileState.PLAYMODE_EXITING) == UnityProfileState.PLAYMODE_EXITING
    assert sm.transition(UnityProfileState.EDITOR_READY) == UnityProfileState.EDITOR_READY


def test_build_can_continue_into_runtime_testing():
    sm = UnityStateMachine(UnityProfileState.EDITOR_READY)
    sm.transition(UnityProfileState.BUILDING)
    sm.transition(UnityProfileState.RUNTIME_STARTING)
    sm.transition(UnityProfileState.RUNTIME_TESTING)
    assert sm.transition(UnityProfileState.EDITOR_READY) == UnityProfileState.EDITOR_READY


def test_busy_state_rejects_unrelated_operation_transition():
    sm = UnityStateMachine(UnityProfileState.COMPILING)
    with pytest.raises(UnityStateTransitionError, match="COMPILING -> BUILDING"):
        sm.transition(UnityProfileState.BUILDING)


def test_user_action_required_requires_recheck_before_ready():
    sm = UnityStateMachine(UnityProfileState.USER_ACTION_REQUIRED)
    with pytest.raises(UnityStateTransitionError):
        sm.transition(UnityProfileState.READY)
    assert sm.transition(UnityProfileState.CHECKING) == UnityProfileState.CHECKING
    assert sm.transition(UnityProfileState.READY) == UnityProfileState.READY


def test_error_requires_checking_for_normal_recovery():
    sm = UnityStateMachine(UnityProfileState.ERROR)
    with pytest.raises(UnityStateTransitionError):
        sm.transition(UnityProfileState.EDITOR_READY)
    assert sm.transition(UnityProfileState.CHECKING) == UnityProfileState.CHECKING


def test_every_declared_unity_state_is_reachable_from_uninitialized():
    frontier = {UnityProfileState.UNINITIALIZED}
    seen = set(frontier)
    while frontier:
        current = frontier.pop()
        for nxt in UnityStateMachine.allowed_from(current):
            if nxt not in seen:
                seen.add(nxt)
                frontier.add(nxt)
    assert seen == set(UnityProfileState)


def test_reset_and_force_error_are_explicit_escape_hatches():
    sm = UnityStateMachine(UnityProfileState.BUILDING)
    assert sm.force_error() == UnityProfileState.ERROR
    assert sm.reset() == UnityProfileState.UNINITIALIZED
