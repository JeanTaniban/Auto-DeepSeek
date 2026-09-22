import json

from clipboard_agent.storage import Settings, SettingsStore


def test_auto_configuration_requires_prompt_send_visual_region_and_template_path():
    settings = Settings()
    assert not settings.auto_configured
    settings.auto_prompt_x = 10
    settings.auto_prompt_y = 20
    settings.auto_send_x = 30
    settings.auto_send_y = 40
    settings.auto_response_left = 100
    settings.auto_response_top = 120
    settings.auto_response_right = 900
    settings.auto_response_bottom = 700
    assert not settings.auto_configured
    settings.auto_copy_template_path = r"C:\\refs\\copy.png"
    assert settings.auto_configured


def test_auto_configuration_does_not_require_legacy_copy_coordinate():
    settings = Settings(
        auto_prompt_x=10,
        auto_prompt_y=20,
        auto_send_x=30,
        auto_send_y=40,
        auto_response_left=100,
        auto_response_top=120,
        auto_response_right=900,
        auto_response_bottom=700,
        auto_copy_template_path=r"C:\\refs\\copy.png",
    )
    assert settings.auto_copy_x is None
    assert settings.auto_copy_y is None
    assert settings.auto_configured


def test_auto_configuration_rejects_inverted_visual_region():
    settings = Settings(
        auto_prompt_x=10,
        auto_prompt_y=20,
        auto_send_x=30,
        auto_send_y=40,
        auto_response_left=900,
        auto_response_top=700,
        auto_response_right=100,
        auto_response_bottom=120,
        auto_copy_template_path=r"C:\\refs\\copy.png",
    )
    assert not settings.auto_configured


def test_auto_settings_defaults_are_conservative():
    settings = Settings()
    assert settings.auto_visual_stable_seconds >= 1
    assert settings.auto_visual_timeout_seconds >= 30
    assert settings.auto_visual_poll_ms >= 100
    assert settings.auto_clipboard_timeout_seconds >= 0.5
    assert 0.5 <= settings.auto_copy_match_threshold <= 0.99
    assert settings.auto_delay_paste_to_send_seconds >= 0
    assert 0 <= settings.auto_timing_jitter_percent <= 50


def test_settings_store_roundtrip_complete_auto_configuration(tmp_path):
    store = SettingsStore(base_dir=tmp_path)
    settings = Settings(
        project_root=r"C:\\work\\demo",
        goal="Build the demo",
        auto_prompt_x=101,
        auto_prompt_y=202,
        auto_send_x=303,
        auto_send_y=404,
        auto_copy_x=505,
        auto_copy_y=606,
        auto_copy_template_path=r"C:\\refs\\deepseek-copy.png",
        auto_copy_match_threshold=0.91,
        auto_response_left=100,
        auto_response_top=120,
        auto_response_right=900,
        auto_response_bottom=700,
        auto_visual_stable_seconds=4.5,
        auto_visual_timeout_seconds=240,
        auto_clipboard_timeout_seconds=2.5,
        auto_delay_result_to_send_seconds=0.4,
        auto_delay_clipboard_to_prompt_seconds=0.2,
        auto_delay_prompt_to_paste_seconds=0.25,
        auto_delay_paste_to_send_seconds=0.8,
        auto_delay_send_to_watch_seconds=0.35,
        auto_delay_stable_to_copy_seconds=0.45,
        auto_timing_jitter_percent=18.0,
        auto_screen_x=0,
        auto_screen_y=0,
        auto_screen_width=1920,
        auto_screen_height=1080,
    )
    store.save(settings)

    loaded = store.load()
    assert loaded == settings
    assert loaded.auto_configured


def test_settings_store_roundtrip_partial_setup(tmp_path):
    """A user may close setup halfway through; captured points must not disappear."""
    store = SettingsStore(base_dir=tmp_path)
    partial = Settings(
        auto_prompt_x=111,
        auto_prompt_y=222,
        auto_visual_stable_seconds=5.0,
        auto_copy_template_path=r"C:\\refs\\copy.png",
        auto_copy_match_threshold=0.88,
    )
    store.save(partial)

    loaded = store.load()
    assert loaded.auto_prompt_x == 111
    assert loaded.auto_prompt_y == 222
    assert loaded.auto_visual_stable_seconds == 5.0
    assert loaded.auto_copy_template_path.endswith("copy.png")
    assert loaded.auto_copy_match_threshold == 0.88
    assert not loaded.auto_configured


def test_target_app_timing_defaults_and_persistence(tmp_path):
    store = SettingsStore(base_dir=tmp_path)
    settings = Settings(
        auto_target_window_timeout_seconds=22.0,
        auto_target_launch_settle_seconds=0.8,
        auto_target_action_delay_seconds=0.35,
        auto_target_close_timeout_seconds=3.2,
        auto_target_restore_delay_seconds=0.6,
        auto_target_attachment_delay_seconds=0.4,
        auto_target_attachment_to_send_seconds=0.7,
    )
    store.save(settings)
    loaded = store.load()
    assert loaded.auto_target_window_timeout_seconds == 22.0
    assert loaded.auto_target_action_delay_seconds == 0.35
    assert loaded.auto_target_attachment_to_send_seconds == 0.7


def test_legacy_timing_profile_is_migrated_to_relaxed_defaults(tmp_path):
    store = SettingsStore(base_dir=tmp_path)
    store.path.write_text(json.dumps({
        "auto_target_window_timeout_seconds": 15.0,
        "auto_target_launch_settle_seconds": 0.6,
        "auto_test_ready_stable_seconds": 1.2,
        "auto_test_activation_settle_seconds": 0.25,
        "auto_clipboard_timeout_seconds": 1.5,
    }), encoding="utf-8")
    loaded = store.load()
    assert loaded.timing_profile_version == 2
    assert loaded.auto_target_window_timeout_seconds >= 60.0
    assert loaded.auto_target_launch_settle_seconds >= 2.0
    assert loaded.auto_test_ready_stable_seconds >= 3.0
    assert loaded.auto_test_activation_settle_seconds >= 1.0
    assert loaded.auto_clipboard_timeout_seconds >= 4.0
    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert persisted["timing_profile_version"] == 2
