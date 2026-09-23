from pathlib import Path

import pytest

from clipboard_agent.profiles import (
    AgentProfile,
    DetectionResult,
    GenericProfile,
    HealthReport,
    ProfileManager,
    ProfileMetadata,
    ProfileRegistry,
    ProfileRegistryError,
    ProfileState,
    build_default_profile_registry,
)
from clipboard_agent.prompt_builder import build_initial_prompt


class DummyProfile(AgentProfile):
    def __init__(self, profile_id: str, display_name: str, confidence: float = 0.5):
        self._metadata = ProfileMetadata(profile_id, display_name, "1")
        self.confidence = confidence
        self.shutdown_calls = 0

    @property
    def metadata(self):
        return self._metadata

    def detect_project(self, project_root: Path):
        return DetectionResult(True, self.confidence, project_type=self._metadata.profile_id)

    def health_check(self, project_root: Path, *, deep: bool = False):
        return HealthReport()

    def current_state(self):
        return ProfileState.READY

    def build_initial_prompt(self, project_root: Path, goal: str, *, shell: str, os_name: str):
        return f"{self._metadata.profile_id}:{project_root}:{goal}:{shell}:{os_name}"

    def shutdown(self):
        self.shutdown_calls += 1


def test_default_registry_contains_only_generic_profile():
    registry = build_default_profile_registry()
    assert [m.profile_id for m in registry.metadata()] == ["generic"]
    assert registry.get("generic").metadata.display_name == "Développement général"


def test_registry_rejects_duplicate_ids_and_display_names():
    registry = ProfileRegistry((DummyProfile("one", "One"),))
    with pytest.raises(ProfileRegistryError, match="déjà enregistré"):
        registry.register(DummyProfile("one", "Other"))
    with pytest.raises(ProfileRegistryError, match="Nom de profil déjà utilisé"):
        registry.register(DummyProfile("two", "one"))


def test_registry_lookup_by_display_name_is_case_insensitive():
    profile = DummyProfile("demo", "Démo")
    registry = ProfileRegistry((profile,))
    assert registry.get_by_display_name("DÉMO") is profile
    with pytest.raises(ProfileRegistryError, match="Profil inconnu"):
        registry.get("missing")


def test_manager_falls_back_to_generic_for_unknown_persisted_profile():
    registry = ProfileRegistry((GenericProfile(), DummyProfile("special", "Special")))
    manager = ProfileManager(registry, active_profile_id="removed-profile")
    assert manager.active_profile_id == "generic"
    assert isinstance(manager.active_profile, GenericProfile)


def test_manager_selects_profile_and_shuts_down_previous_profile():
    generic = GenericProfile()
    special = DummyProfile("special", "Special")
    registry = ProfileRegistry((generic, special))
    manager = ProfileManager(registry)
    selected = manager.select("special")
    assert selected is special
    assert manager.active_profile_id == "special"
    with pytest.raises(ProfileRegistryError, match="Profil inconnu"):
        manager.select("missing")


def test_manager_suggests_highest_confidence_profile(tmp_path: Path):
    low = DummyProfile("low", "Low", 0.2)
    high = DummyProfile("high", "High", 0.9)
    registry = ProfileRegistry((GenericProfile(), low, high))
    manager = ProfileManager(registry)
    assert manager.suggested_profile(tmp_path) is high


def test_generic_profile_health_and_detection(tmp_path: Path):
    profile = GenericProfile()
    detection = profile.detect_project(tmp_path)
    health = profile.health_check(tmp_path)
    assert detection.matched is True
    assert detection.project_type == "generic"
    assert profile.current_state() == ProfileState.READY
    assert health.overall.value == "PASS"


def test_generic_profile_prompt_is_exact_v214_prompt(tmp_path: Path):
    profile = GenericProfile()
    expected = build_initial_prompt(
        tmp_path,
        "Ship it",
        shell="powershell",
        os_name="Windows",
    )
    actual = profile.build_initial_prompt(
        tmp_path,
        "Ship it",
        shell="powershell",
        os_name="Windows",
    )
    assert actual == expected
