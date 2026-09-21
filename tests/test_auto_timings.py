from types import SimpleNamespace

from clipboard_agent.app import ClipboardAgentApp, jittered_action_delay_ms
from clipboard_agent.state_machine import AutoState
from clipboard_agent.visual_watch import VisualState


def test_jittered_action_delay_is_exact_when_disabled():
    assert jittered_action_delay_ms(0.30, 0) == 300
    assert jittered_action_delay_ms(0.0, 50) == 0


def test_jittered_action_delay_stays_within_configured_bounds():
    class EdgeRng:
        def __init__(self, value):
            self.value = value

        def uniform(self, low, high):
            assert low == -0.20
            assert high == 0.20
            return self.value

    assert jittered_action_delay_ms(1.0, 20, EdgeRng(-0.20)) == 800
    assert jittered_action_delay_ms(1.0, 20, EdgeRng(0.20)) == 1200


def test_jitter_percent_is_clamped_to_fifty_percent():
    class HighRng:
        def uniform(self, low, high):
            assert low == -0.50
            assert high == 0.50
            return high

    assert jittered_action_delay_ms(2.0, 500, HighRng()) == 3000


def test_auto_send_sequence_is_causal_and_uses_each_configured_delay():
    events = []
    delays = {
        "auto_delay_clipboard_to_prompt_seconds": 110,
        "auto_delay_prompt_to_paste_seconds": 220,
        "auto_delay_paste_to_send_seconds": 330,
        "auto_delay_send_to_watch_seconds": 440,
    }

    class Desktop:
        def click(self, point):
            events.append(("click", point))

        def paste(self):
            events.append(("paste",))

    class FakeApp:
        auto_enabled = True
        auto_paused = False
        auto_visual_tracker = object()
        desktop = Desktop()
        _auto_click_prompt_for_send = ClipboardAgentApp._auto_click_prompt_for_send
        _auto_paste_for_send = ClipboardAgentApp._auto_paste_for_send
        _auto_send_after_paste = ClipboardAgentApp._auto_send_after_paste

        def _transition_auto(self, target):
            events.append(("transition", target))
            return True

        def _write_clipboard(self, text):
            events.append(("clipboard", text))

        def _auto_action_delay_ms(self, setting_name):
            return delays[setting_name]

        def _schedule_auto(self, delay, callback):
            events.append(("schedule", delay))
            callback()
            return "job"

        def _auto_point(self, name):
            return name

        def _auto_prepare_visual_baseline(self):
            events.append(("baseline",))

        def _auto_begin_visual_wait(self):
            events.append(("visual_wait",))

        def _set_status(self, *_args):
            pass

    fake = FakeApp()
    ClipboardAgentApp._auto_send_message(fake, "RESULT")

    assert events == [
        ("transition", AutoState.SENDING),
        ("clipboard", "RESULT"),
        ("schedule", 110),
        ("click", "prompt"),
        ("schedule", 220),
        ("paste",),
        ("schedule", 330),
        ("baseline",),
        ("click", "send"),
        ("schedule", 440),
        ("visual_wait",),
    ]


def test_visual_stability_uses_configured_delay_before_copy():
    events = []

    class Tracker:
        def observe(self, _frame, _now):
            return SimpleNamespace(
                state=VisualState.STABLE,
                stable_for=3.2,
                seen_motion=True,
                motion_ratio=0.0,
            )

    class Desktop:
        def capture_signature(self, _rect):
            return b"frame"

    class FakeApp:
        auto_state = AutoState.WAITING_VISUAL
        auto_visual_tracker = Tracker()
        desktop = Desktop()
        settings = SimpleNamespace(auto_visual_stable_seconds=3.0, auto_visual_poll_ms=300)

        def _auto_response_rect(self):
            return object()

        def _auto_action_delay_ms(self, name):
            assert name == "auto_delay_stable_to_copy_seconds"
            return 650

        def _schedule_auto(self, delay, callback):
            events.append((delay, callback))

        def _auto_click_copy(self):
            pass

        def _set_status(self, *_args):
            pass

    fake = FakeApp()
    ClipboardAgentApp._auto_visual_tick(fake)
    assert fake.auto_visual_tracker is None
    assert events[0][0] == 650
    assert events[0][1] == fake._auto_click_copy


def test_auto_send_with_visual_attachment_pastes_text_then_image_then_send():
    events = []
    image = object()

    class Desktop:
        def click(self, point):
            events.append(("click", point))

        def paste(self):
            events.append(("paste",))

        def set_clipboard_image_bgr(self, value):
            assert value is image
            events.append(("image_clipboard",))

    class FakeApp:
        auto_enabled = True
        auto_paused = False
        auto_visual_tracker = object()
        auto_pending_attachment = None
        desktop = Desktop()
        settings = SimpleNamespace(
            auto_target_attachment_delay_seconds=0.25,
            auto_target_attachment_to_send_seconds=0.55,
        )
        _auto_click_prompt_for_send = ClipboardAgentApp._auto_click_prompt_for_send
        _auto_paste_for_send = ClipboardAgentApp._auto_paste_for_send
        _auto_paste_attachment_for_send = ClipboardAgentApp._auto_paste_attachment_for_send
        _auto_send_after_paste = ClipboardAgentApp._auto_send_after_paste

        def _transition_auto(self, target):
            events.append(("transition", target))
            return True

        def _write_clipboard(self, text):
            events.append(("clipboard", text))

        def _auto_action_delay_ms(self, setting_name):
            return {
                "auto_delay_clipboard_to_prompt_seconds": 100,
                "auto_delay_prompt_to_paste_seconds": 200,
                "auto_delay_send_to_watch_seconds": 400,
            }[setting_name]

        def _schedule_auto(self, delay, callback):
            events.append(("schedule", delay))
            callback()
            return "job"

        def _auto_point(self, name):
            return name

        def _auto_prepare_visual_baseline(self):
            events.append(("baseline",))

        def _auto_begin_visual_wait(self):
            events.append(("visual_wait",))

        def _set_status(self, *_args):
            pass

    fake = FakeApp()
    ClipboardAgentApp._auto_send_message(fake, "#MultipleResult", image)
    assert events == [
        ("transition", AutoState.SENDING),
        ("clipboard", "#MultipleResult"),
        ("schedule", 100),
        ("click", "prompt"),
        ("schedule", 200),
        ("paste",),
        ("schedule", 250),
        ("image_clipboard",),
        ("paste",),
        ("schedule", 550),
        ("baseline",),
        ("click", "send"),
        ("schedule", 400),
        ("visual_wait",),
    ]
