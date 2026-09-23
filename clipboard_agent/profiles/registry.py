from __future__ import annotations

from collections.abc import Iterable

from .base import AgentProfile


class ProfileRegistryError(ValueError):
    pass


class ProfileRegistry:
    def __init__(self, profiles: Iterable[AgentProfile] = ()) -> None:
        self._profiles: dict[str, AgentProfile] = {}
        self._display_names: dict[str, str] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: AgentProfile) -> None:
        metadata = profile.metadata
        profile_id = metadata.profile_id.strip()
        display_name = metadata.display_name.strip()
        if not profile_id:
            raise ProfileRegistryError("Un profil doit avoir un identifiant non vide.")
        if not display_name:
            raise ProfileRegistryError(f"Le profil {profile_id!r} doit avoir un nom affiché non vide.")
        if profile_id in self._profiles:
            raise ProfileRegistryError(f"Profil déjà enregistré : {profile_id}")
        existing_id = self._display_names.get(display_name.casefold())
        if existing_id is not None:
            raise ProfileRegistryError(
                f"Nom de profil déjà utilisé : {display_name} ({existing_id})"
            )
        self._profiles[profile_id] = profile
        self._display_names[display_name.casefold()] = profile_id

    def get(self, profile_id: str) -> AgentProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise ProfileRegistryError(f"Profil inconnu : {profile_id}") from exc

    def get_by_display_name(self, display_name: str) -> AgentProfile:
        profile_id = self._display_names.get(display_name.strip().casefold())
        if profile_id is None:
            raise ProfileRegistryError(f"Profil inconnu : {display_name}")
        return self._profiles[profile_id]

    def contains(self, profile_id: str) -> bool:
        return profile_id in self._profiles

    def profiles(self) -> tuple[AgentProfile, ...]:
        return tuple(self._profiles.values())

    def metadata(self):
        return tuple(profile.metadata for profile in self._profiles.values())
