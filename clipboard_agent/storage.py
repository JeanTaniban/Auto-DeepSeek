from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Settings:
    project_root: str = ""
    goal: str = ""
    shell: str = "powershell"
    profile_id: str = "generic"
    auto_run_low_risk: bool = True
    auto_repair_self: bool = False
    goal_reminder_every: int = 4
    max_output_chars: int = 100_000
    timing_profile_version: int = 2

    # Agent Auto Windows settings. Coordinates are physical desktop pixels.
    auto_prompt_x: int | None = None
    auto_prompt_y: int | None = None
    auto_send_x: int | None = None
    auto_send_y: int | None = None
    auto_copy_x: int | None = None  # legacy V2 fixed click, no longer required
    auto_copy_y: int | None = None  # legacy V2 fixed click, no longer required
    auto_copy_template_path: str = ""
    auto_copy_match_threshold: float = 0.86
    auto_response_left: int | None = None
    auto_response_top: int | None = None
    auto_response_right: int | None = None
    auto_response_bottom: int | None = None
    auto_visual_stable_seconds: float = 4.0
    auto_visual_timeout_seconds: float = 240.0
    auto_visual_poll_ms: int = 350
    auto_response_wait_seconds: float = 10.0  # legacy V2 setting, kept for migration
    auto_clipboard_timeout_seconds: float = 4.0

    # Reliability-first pacing. These can be tightened after machine validation.
    auto_delay_result_to_send_seconds: float = 0.60
    auto_delay_clipboard_to_prompt_seconds: float = 0.35
    auto_delay_prompt_to_paste_seconds: float = 0.40
    auto_delay_paste_to_send_seconds: float = 0.80
    auto_delay_send_to_watch_seconds: float = 0.60
    auto_delay_stable_to_copy_seconds: float = 0.50
    auto_timing_jitter_percent: float = 0.0

    # Temporary Target App session (#Multiple).
    auto_target_window_timeout_seconds: float = 60.0
    auto_target_launch_settle_seconds: float = 2.0
    auto_target_action_delay_seconds: float = 0.50
    auto_target_close_timeout_seconds: float = 8.0
    auto_target_restore_delay_seconds: float = 1.0
    auto_target_attachment_delay_seconds: float = 0.75
    auto_target_attachment_to_send_seconds: float = 1.0

    # Persistent TestSession readiness / workspace pacing.
    auto_test_ready_stable_seconds: float = 3.0
    auto_test_ready_poll_ms: int = 350
    auto_test_activation_settle_seconds: float = 1.0

    auto_screen_x: int | None = None
    auto_screen_y: int | None = None
    auto_screen_width: int | None = None
    auto_screen_height: int | None = None

    @property
    def auto_configured(self) -> bool:
        values = (
            self.auto_prompt_x,
            self.auto_prompt_y,
            self.auto_send_x,
            self.auto_send_y,
            self.auto_response_left,
            self.auto_response_top,
            self.auto_response_right,
            self.auto_response_bottom,
        )
        if not all(value is not None for value in values):
            return False
        if not str(self.auto_copy_template_path or "").strip():
            return False
        return (
            int(self.auto_response_right) > int(self.auto_response_left)
            and int(self.auto_response_bottom) > int(self.auto_response_top)
        )


class SettingsStore:
    def __init__(self, app_name: str = "ClipboardAgentRelay", base_dir: Path | str | None = None) -> None:
        # ``base_dir`` exists primarily to make persistence deterministic in tests
        # and portable builds. Production keeps the historical per-user location.
        del app_name  # retained for backwards-compatible constructor calls
        base = Path(base_dir).expanduser() if base_dir is not None else Path.home() / ".clipboard_agent_relay"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / "settings.json"

    @staticmethod
    def _migrate_relaxed_timings(settings: Settings, source_version: int) -> Settings:
        if int(source_version or 0) >= 2:
            return settings
        settings.auto_visual_stable_seconds = max(float(settings.auto_visual_stable_seconds), 4.0)
        settings.auto_visual_timeout_seconds = max(float(settings.auto_visual_timeout_seconds), 240.0)
        settings.auto_visual_poll_ms = max(int(settings.auto_visual_poll_ms), 350)
        settings.auto_clipboard_timeout_seconds = max(float(settings.auto_clipboard_timeout_seconds), 4.0)
        settings.auto_delay_result_to_send_seconds = max(float(settings.auto_delay_result_to_send_seconds), 0.60)
        settings.auto_delay_clipboard_to_prompt_seconds = max(float(settings.auto_delay_clipboard_to_prompt_seconds), 0.35)
        settings.auto_delay_prompt_to_paste_seconds = max(float(settings.auto_delay_prompt_to_paste_seconds), 0.40)
        settings.auto_delay_paste_to_send_seconds = max(float(settings.auto_delay_paste_to_send_seconds), 0.80)
        settings.auto_delay_send_to_watch_seconds = max(float(settings.auto_delay_send_to_watch_seconds), 0.60)
        settings.auto_delay_stable_to_copy_seconds = max(float(settings.auto_delay_stable_to_copy_seconds), 0.50)
        settings.auto_target_window_timeout_seconds = max(float(settings.auto_target_window_timeout_seconds), 60.0)
        settings.auto_target_launch_settle_seconds = max(float(settings.auto_target_launch_settle_seconds), 2.0)
        settings.auto_target_action_delay_seconds = max(float(settings.auto_target_action_delay_seconds), 0.50)
        settings.auto_target_close_timeout_seconds = max(float(settings.auto_target_close_timeout_seconds), 8.0)
        settings.auto_target_restore_delay_seconds = max(float(settings.auto_target_restore_delay_seconds), 1.0)
        settings.auto_target_attachment_delay_seconds = max(float(settings.auto_target_attachment_delay_seconds), 0.75)
        settings.auto_target_attachment_to_send_seconds = max(float(settings.auto_target_attachment_to_send_seconds), 1.0)
        settings.auto_test_ready_stable_seconds = max(float(settings.auto_test_ready_stable_seconds), 3.0)
        settings.auto_test_ready_poll_ms = max(int(settings.auto_test_ready_poll_ms), 350)
        settings.auto_test_activation_settle_seconds = max(float(settings.auto_test_activation_settle_seconds), 1.0)
        settings.timing_profile_version = 2
        return settings

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            allowed = {k: data[k] for k in Settings.__annotations__ if k in data}
            settings = Settings(**allowed)
            source_version = int(data.get("timing_profile_version", 0) or 0)
            settings = self._migrate_relaxed_timings(settings, source_version)
            if source_version < settings.timing_profile_version:
                self.save(settings)
            return settings
        except Exception:
            return Settings()

    def save(self, settings: Settings) -> None:
        """Persist settings atomically so a crash cannot leave a half-written JSON file."""
        payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(payload, encoding="utf-8")
        temp.replace(self.path)
