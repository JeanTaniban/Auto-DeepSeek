from clipboard_agent.visual_watch import VisualStabilityTracker, VisualState, motion_ratio


def frame(pixel=(0, 0, 0, 255), count=100):
    return bytes(pixel) * count


def changed_frame(baseline: bytes, changed_pixels: int = 20) -> bytes:
    changed = bytearray(baseline)
    for i in range(0, changed_pixels * 4, 4):
        changed[i] = 255
    return bytes(changed)


def test_motion_ratio_ignores_small_render_noise():
    a = frame((20, 20, 20, 255), 10)
    b = frame((28, 25, 31, 255), 10)
    assert motion_ratio(a, b, channel_delta=18) == 0.0


def test_motion_ratio_detects_significant_change():
    a = frame((0, 0, 0, 255), 100)
    b = bytearray(a)
    for i in range(0, 40, 4):
        b[i] = 255
    assert motion_ratio(a, bytes(b), channel_delta=18) == 0.1


def test_stability_requires_motion_before_completion():
    tracker = VisualStabilityTracker(stable_seconds=2, timeout_seconds=20, motion_threshold=0.01)
    baseline = frame(count=100)
    tracker.start(baseline, 0)
    obs = tracker.observe(baseline, 5)
    assert obs.state == VisualState.WAITING
    assert not obs.seen_motion


def test_motion_then_stability_completes():
    tracker = VisualStabilityTracker(stable_seconds=2, timeout_seconds=20, motion_threshold=0.01)
    baseline = frame(count=100)
    changed = changed_frame(baseline)
    tracker.start(baseline, 0)
    assert tracker.observe(changed, 1).state == VisualState.MOVING
    assert tracker.observe(changed, 2).state == VisualState.WAITING
    obs = tracker.observe(changed, 3.1)
    assert obs.state == VisualState.STABLE
    assert obs.stable_for >= 2


def test_timeout_when_nothing_ever_moves():
    tracker = VisualStabilityTracker(stable_seconds=2, timeout_seconds=5, motion_threshold=0.01)
    baseline = frame(count=100)
    tracker.start(baseline, 0)
    obs = tracker.observe(baseline, 5.1)
    assert obs.state == VisualState.TIMEOUT


def test_initial_attachment_accepts_already_static_reply():
    tracker = VisualStabilityTracker(
        stable_seconds=2,
        timeout_seconds=20,
        motion_threshold=0.01,
        require_motion=False,
    )
    baseline = frame(count=100)
    tracker.start(baseline, 0)
    assert tracker.observe(baseline, 1.9).state == VisualState.WAITING
    obs = tracker.observe(baseline, 2.1)
    assert obs.state == VisualState.STABLE
    assert not obs.seen_motion


def test_initial_attachment_waits_for_moving_reply_to_settle():
    tracker = VisualStabilityTracker(
        stable_seconds=2,
        timeout_seconds=20,
        motion_threshold=0.01,
        require_motion=False,
    )
    baseline = frame(count=100)
    moving = changed_frame(baseline)
    tracker.start(baseline, 0)
    assert tracker.observe(moving, 1).state == VisualState.MOVING
    assert tracker.observe(moving, 2.9).state == VisualState.WAITING
    obs = tracker.observe(moving, 3.1)
    assert obs.state == VisualState.STABLE
    assert obs.seen_motion
