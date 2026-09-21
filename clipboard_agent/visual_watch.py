from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VisualState(str, Enum):
    WAITING = "WAITING"
    MOVING = "MOVING"
    STABLE = "STABLE"
    TIMEOUT = "TIMEOUT"


def motion_ratio(previous: bytes, current: bytes, *, channel_delta: int = 18) -> float:
    """Return the fraction of BGRA pixels that changed significantly.

    The alpha byte is ignored. Small per-channel variations are ignored so font
    antialiasing, video noise and tiny rendering fluctuations do not keep the
    visual watcher alive forever.
    """
    if not previous or not current or len(previous) != len(current) or len(current) % 4:
        return 1.0

    changed = 0
    pixels = len(current) // 4
    for offset in range(0, len(current), 4):
        if (
            abs(previous[offset] - current[offset]) > channel_delta
            or abs(previous[offset + 1] - current[offset + 1]) > channel_delta
            or abs(previous[offset + 2] - current[offset + 2]) > channel_delta
        ):
            changed += 1
    return changed / max(1, pixels)


@dataclass(slots=True)
class VisualObservation:
    state: VisualState
    motion_ratio: float
    stable_for: float
    elapsed: float
    seen_motion: bool


class VisualStabilityTracker:
    """Track motion in a fixed screen region until it becomes stably static.

    Normal answer cycles use ``require_motion=True``: stability is accepted only
    after significant motion has been observed since the pre-send baseline.

    Initial Agent Auto attachment uses ``require_motion=False``: the currently
    displayed assistant answer may already be complete when Auto starts, so a
    region that stays static for ``stable_seconds`` is allowed to complete.
    """

    def __init__(
        self,
        *,
        stable_seconds: float = 3.0,
        timeout_seconds: float = 180.0,
        motion_threshold: float = 0.0004,
        channel_delta: int = 18,
        require_motion: bool = True,
    ) -> None:
        self.stable_seconds = max(0.5, float(stable_seconds))
        self.timeout_seconds = max(self.stable_seconds + 1.0, float(timeout_seconds))
        self.motion_threshold = max(0.0001, min(1.0, float(motion_threshold)))
        self.channel_delta = max(1, min(255, int(channel_delta)))
        self.require_motion = bool(require_motion)
        self.previous: bytes | None = None
        self.started_at = 0.0
        self.last_motion_at = 0.0
        self.seen_motion = False

    def start(self, baseline: bytes, now: float) -> None:
        if not baseline:
            raise ValueError("La capture visuelle de référence est vide.")
        self.previous = baseline
        self.started_at = float(now)
        self.last_motion_at = float(now)
        self.seen_motion = False

    def observe(self, frame: bytes, now: float) -> VisualObservation:
        if self.previous is None:
            raise RuntimeError("VisualStabilityTracker.start() doit être appelé avant observe().")
        now = float(now)
        elapsed = max(0.0, now - self.started_at)
        ratio = motion_ratio(self.previous, frame, channel_delta=self.channel_delta)
        self.previous = frame

        if ratio >= self.motion_threshold:
            self.seen_motion = True
            self.last_motion_at = now
            state = VisualState.MOVING
            stable_for = 0.0
        else:
            stable_for = max(0.0, now - self.last_motion_at)
            can_finish = self.seen_motion or not self.require_motion
            if can_finish and stable_for >= self.stable_seconds:
                state = VisualState.STABLE
            else:
                state = VisualState.WAITING

        if elapsed >= self.timeout_seconds and state != VisualState.STABLE:
            state = VisualState.TIMEOUT

        return VisualObservation(
            state=state,
            motion_ratio=ratio,
            stable_for=stable_for,
            elapsed=elapsed,
            seen_motion=self.seen_motion,
        )
