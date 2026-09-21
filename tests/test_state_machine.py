import pytest

from clipboard_agent.state_machine import AutoState, AutoStateMachine, AutoTransitionError


def test_nominal_initial_sync_and_execution_cycle():
    sm = AutoStateMachine()
    assert sm.transition(AutoState.STARTING) == AutoState.STARTING
    assert sm.transition(AutoState.SYNCING_EXISTING_REPLY) == AutoState.SYNCING_EXISTING_REPLY
    assert sm.transition(AutoState.WAITING_INITIAL_CLIPBOARD) == AutoState.WAITING_INITIAL_CLIPBOARD
    assert sm.transition(AutoState.PROCESSING_INITIAL_REPLY) == AutoState.PROCESSING_INITIAL_REPLY
    assert sm.transition(AutoState.EXECUTING) == AutoState.EXECUTING
    assert sm.transition(AutoState.SENDING) == AutoState.SENDING
    assert sm.transition(AutoState.WAITING_VISUAL) == AutoState.WAITING_VISUAL
    assert sm.transition(AutoState.WAITING_CLIPBOARD) == AutoState.WAITING_CLIPBOARD
    assert sm.transition(AutoState.PROCESSING_REPLY) == AutoState.PROCESSING_REPLY


def test_initial_duplicate_can_recover_last_result_then_resume_normal_cycle():
    sm = AutoStateMachine()
    sm.transition(AutoState.STARTING)
    sm.transition(AutoState.SYNCING_EXISTING_REPLY)
    sm.transition(AutoState.WAITING_INITIAL_CLIPBOARD)
    sm.transition(AutoState.PROCESSING_INITIAL_REPLY)
    assert sm.transition(AutoState.RECOVERING_LAST_RESULT) == AutoState.RECOVERING_LAST_RESULT
    assert sm.transition(AutoState.SENDING) == AutoState.SENDING
    assert sm.transition(AutoState.WAITING_VISUAL) == AutoState.WAITING_VISUAL
    assert sm.transition(AutoState.WAITING_CLIPBOARD) == AutoState.WAITING_CLIPBOARD
    assert sm.transition(AutoState.PROCESSING_REPLY) == AutoState.PROCESSING_REPLY


def test_normal_cycle_cannot_enter_initial_recovery_branch():
    sm = AutoStateMachine(AutoState.PROCESSING_REPLY)
    with pytest.raises(AutoTransitionError, match="PROCESSING_REPLY -> RECOVERING_LAST_RESULT"):
        sm.transition(AutoState.RECOVERING_LAST_RESULT)


def test_user_intervention_can_pause_from_every_active_state():
    active_states = [
        AutoState.STARTING,
        AutoState.SYNCING_EXISTING_REPLY,
        AutoState.WAITING_INITIAL_CLIPBOARD,
        AutoState.PROCESSING_INITIAL_REPLY,
        AutoState.RECOVERING_LAST_RESULT,
        AutoState.WAITING_CLIPBOARD,
        AutoState.PROCESSING_REPLY,
        AutoState.EXECUTING,
        AutoState.SENDING,
        AutoState.WAITING_VISUAL,
        AutoState.TARGET_STARTING,
        AutoState.TARGET_RUNNING,
        AutoState.TARGET_RESTORING,
    ]
    for state in active_states:
        sm = AutoStateMachine(state)
        assert sm.transition(AutoState.PAUSED) == AutoState.PAUSED
        assert sm.transition(AutoState.OFF) == AutoState.OFF


def test_illegal_transition_is_rejected():
    sm = AutoStateMachine()
    with pytest.raises(AutoTransitionError, match="OFF -> EXECUTING"):
        sm.transition(AutoState.EXECUTING)


def test_paused_cannot_resume_implicitly():
    sm = AutoStateMachine(AutoState.PAUSED)
    with pytest.raises(AutoTransitionError):
        sm.transition(AutoState.SENDING)


def test_force_off_is_idempotent_fail_safe():
    sm = AutoStateMachine(AutoState.EXECUTING)
    assert sm.force_off() == AutoState.OFF
    assert sm.force_off() == AutoState.OFF


def test_all_states_are_reachable_from_off():
    frontier = {AutoState.OFF}
    seen = set(frontier)
    while frontier:
        current = frontier.pop()
        for nxt in AutoStateMachine.allowed_from(current):
            if nxt not in seen:
                seen.add(nxt)
                frontier.add(nxt)
    assert seen == set(AutoState)


def test_every_active_state_has_fail_safe_path_to_off():
    for state in AutoState:
        if state == AutoState.OFF:
            continue
        assert AutoState.OFF in AutoStateMachine.allowed_from(state)


def test_ui_transition_helper_fails_closed_on_impossible_transition():
    from clipboard_agent.app import ClipboardAgentApp

    class FakeApp:
        auto_machine = AutoStateMachine()

        def _stop_auto(self, reason):
            self.stop_reason = reason
            self.auto_machine.force_off()

        def _append_terminal(self, text, tag=None):
            self.log = (text, tag)

    fake = FakeApp()
    ok = ClipboardAgentApp._transition_auto(fake, AutoState.EXECUTING)
    assert ok is False
    assert fake.auto_machine.state == AutoState.OFF
    assert "Incohérence interne" in fake.stop_reason
    assert "OFF -> EXECUTING" in fake.log[0]


def test_target_app_sequence_transitions_back_to_sending():
    sm = AutoStateMachine(AutoState.PROCESSING_REPLY)
    assert sm.transition(AutoState.TARGET_STARTING) == AutoState.TARGET_STARTING
    assert sm.transition(AutoState.TARGET_RUNNING) == AutoState.TARGET_RUNNING
    assert sm.transition(AutoState.TARGET_RESTORING) == AutoState.TARGET_RESTORING
    assert sm.transition(AutoState.SENDING) == AutoState.SENDING
    assert sm.transition(AutoState.WAITING_VISUAL) == AutoState.WAITING_VISUAL


def test_target_restoring_cannot_jump_directly_to_waiting_visual():
    sm = AutoStateMachine(AutoState.TARGET_RESTORING)
    with pytest.raises(AutoTransitionError):
        sm.transition(AutoState.WAITING_VISUAL)


def test_persistent_test_session_open_action_close_cycles():
    sm = AutoStateMachine(AutoState.PROCESSING_REPLY)
    assert sm.transition(AutoState.TEST_OPENING) == AutoState.TEST_OPENING
    assert sm.transition(AutoState.TEST_RESTORING) == AutoState.TEST_RESTORING
    assert sm.transition(AutoState.SENDING) == AutoState.SENDING
    assert sm.transition(AutoState.WAITING_VISUAL) == AutoState.WAITING_VISUAL
    assert sm.transition(AutoState.WAITING_CLIPBOARD) == AutoState.WAITING_CLIPBOARD
    assert sm.transition(AutoState.PROCESSING_REPLY) == AutoState.PROCESSING_REPLY
    assert sm.transition(AutoState.TEST_ACTING) == AutoState.TEST_ACTING
    assert sm.transition(AutoState.TEST_RESTORING) == AutoState.TEST_RESTORING
    assert sm.transition(AutoState.SENDING) == AutoState.SENDING


def test_test_session_states_all_support_user_pause_and_fail_safe_off():
    for state in (AutoState.TEST_OPENING, AutoState.TEST_ACTING, AutoState.TEST_RESTORING, AutoState.TEST_CLOSING):
        sm = AutoStateMachine(state)
        assert sm.transition(AutoState.PAUSED) == AutoState.PAUSED
        assert sm.transition(AutoState.OFF) == AutoState.OFF
