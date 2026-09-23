from __future__ import annotations

import os
import platform
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .app import ClipboardAgentApp, PANEL, MUTED
from .profiles import ProfileManager, ProfileRegistryError, build_default_profile_registry
from .test_session import TestSessionState
from .win32_input import enable_per_monitor_dpi_awareness


class ProfiledClipboardAgentApp(ClipboardAgentApp):
    """ClipboardAgentApp with the generic profile layer enabled.

    P0 deliberately keeps the existing application implementation untouched and
    inserts the profile boundary around prompt construction and persisted UI
    selection. Later phases can move more responsibilities behind profiles
    without making the V2.14 behavior change in the same refactor.
    """

    def _ensure_profile_manager(self) -> ProfileManager:
        manager = getattr(self, "profile_manager", None)
        if manager is None:
            manager = ProfileManager(
                build_default_profile_registry(),
                active_profile_id=getattr(self.settings, "profile_id", "generic"),
            )
            self.profile_manager = manager
            # An unknown profile persisted by a future/removed build falls back
            # deterministically to GenericProfile instead of preventing startup.
            self.settings.profile_id = manager.active_profile_id
        return manager

    def _build_ui(self) -> None:
        super()._build_ui()
        manager = self._ensure_profile_manager()

        project_panel = self.project_entry.master
        profile_row = ttk.Frame(project_panel, style="Panel.TFrame")
        profile_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        profile_row.columnconfigure(1, weight=1)

        ttk.Label(profile_row, text="Profil", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.profile_var = tk.StringVar(
            value=manager.active_profile.metadata.display_name
        )
        values = tuple(meta.display_name for meta in manager.registry.metadata())
        self.profile_combo = ttk.Combobox(
            profile_row,
            textvariable=self.profile_var,
            values=values,
            state="readonly",
            width=30,
        )
        self.profile_combo.grid(row=0, column=1, sticky="w")
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)

        self.profile_status_var = tk.StringVar(value="")
        ttk.Label(
            profile_row,
            textvariable=self.profile_status_var,
            style="Muted.TLabel",
        ).grid(row=0, column=2, sticky="e", padx=(12, 0))
        self._refresh_profile_status()

    def _restore_settings(self) -> None:
        super()._restore_settings()
        manager = self._ensure_profile_manager()
        if hasattr(self, "profile_var"):
            self.profile_var.set(manager.active_profile.metadata.display_name)
        self._refresh_profile_status()

    def _save_settings(self) -> None:
        manager = self._ensure_profile_manager()
        self.settings.profile_id = manager.active_profile_id
        super()._save_settings()

    def _profile_switch_blocked(self) -> bool:
        return bool(
            self.auto_enabled
            or self.executor.running
            or self.target_runner.running
            or self.test_session.state != TestSessionState.CLOSED
        )

    def _on_profile_selected(self, _event=None) -> None:
        manager = self._ensure_profile_manager()
        previous_name = manager.active_profile.metadata.display_name
        requested_name = self.profile_var.get().strip()

        if self._profile_switch_blocked():
            self.profile_var.set(previous_name)
            messagebox.showwarning(
                "Changement de profil indisponible",
                "Arrêtez Agent Auto et fermez toute commande/Target App/TestSession avant de changer de profil.",
            )
            return

        try:
            manager.select_by_display_name(requested_name)
        except ProfileRegistryError as exc:
            self.profile_var.set(previous_name)
            messagebox.showerror("Profil invalide", str(exc))
            return

        self.settings.profile_id = manager.active_profile_id
        self.store.save(self.settings)
        self._refresh_profile_status()

    def _refresh_profile_status(self) -> None:
        if not hasattr(self, "profile_status_var"):
            return
        manager = self._ensure_profile_manager()
        profile = manager.active_profile
        self.profile_status_var.set(
            f"{profile.current_state().value} · v{profile.metadata.version}"
        )

    def _build_prompt(self) -> str:
        root = self._project_root()
        shell = "powershell" if os.name == "nt" else "bash"
        manager = self._ensure_profile_manager()
        return manager.active_profile.build_initial_prompt(
            root,
            self.goal_text.get("1.0", "end"),
            shell=shell,
            os_name=platform.system(),
        )


def main() -> None:
    # Must happen before Tk creates any HWND. Otherwise Windows can virtualize
    # GUI coordinates while GDI/SendInput operate in physical pixels.
    enable_per_monitor_dpi_awareness()
    app = ProfiledClipboardAgentApp()
    app.mainloop()


if __name__ == "__main__":
    main()
