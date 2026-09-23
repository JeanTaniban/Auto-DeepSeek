from clipboard_agent.storage import Settings, SettingsStore


def test_profile_defaults_to_generic():
    assert Settings().profile_id == "generic"


def test_profile_selection_roundtrips_in_settings(tmp_path):
    store = SettingsStore(base_dir=tmp_path)
    settings = Settings(profile_id="future-profile")
    store.save(settings)
    assert store.load().profile_id == "future-profile"


def test_legacy_settings_without_profile_migrate_by_dataclass_default(tmp_path):
    store = SettingsStore(base_dir=tmp_path)
    store.path.write_text('{"project_root":"C:/demo","timing_profile_version":2}', encoding="utf-8")
    loaded = store.load()
    assert loaded.project_root == "C:/demo"
    assert loaded.profile_id == "generic"
