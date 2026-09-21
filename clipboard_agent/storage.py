from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Settings:
    project_root: str = ""
    goal: str = ""
    shell: str = "powershell"
    auto_run_low_risk: bool = True
    goal_reminder_every: int = 4
    max_output_chars: int = 100_000

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
    auto_visual_stable_seconds: float = 3.0
    auto_visual_timeout_seconds: float = 180.0
    auto_visual_poll_ms: int = 300
    auto_response_wait_seconds: float = 10.0  # legacy V2 setting, kept for migration
    auto_clipboard_timeout_seconds: float = 1.5

    # Action pacing. These delays are distinct from timeouts/stability criteria:
    # they only space UI actions so the browser has time to focus, paste and
    # repaint. A bounded percentage jitter may be applied to these six values.
    auto_delay_result_to_send_seconds: float = 0.25
    auto_delay_clipboard_to_prompt_seconds: float = 0.10
    auto_delay_prompt_to_paste_seconds: float = 0.16
    auto_delay_paste_to_send_seconds: float = 0.30
    auto_delay_send_to_watch_seconds: float = 0.20
    auto_delay_stable_to_copy_seconds: float = 0.15
    auto_timing_jitter_percent: float = 0.0

    # Temporary Target App session (#Multiple). These values only pace local
    # development-app interactions and restoration of the browser window.
    auto_target_window_timeout_seconds: float = 15.0
    auto_target_launch_settle_seconds: float = 0.6
    auto_target_action_delay_seconds: float = 0.20
    auto_target_close_timeout_seconds: float = 2.5
    auto_target_restore_delay_seconds: float = 0.35
    auto_target_attachment_delay_seconds: float = 0.30
    auto_target_attachment_to_send_seconds: float = 0.45

    # Persistent TestSession readiness / workspace pacing.
    auto_test_ready_stable_seconds: float = 1.2
    auto_test_ready_poll_ms: int = 250
    auto_test_activation_settle_seconds: float = 0.25

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

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            allowed = {k: data[k] for k in Settings.__annotations__ if k in data}
            return Settings(**allowed)
        except Exception:
            return Settings()

    def save(self, settings: Settings) -> None:
        """Persist settings atomically so a crash cannot leave a half-written JSON file."""
        payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(payload, encoding="utf-8")
        temp.replace(self.path)
