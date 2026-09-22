from __future__ import annotations

import os
import platform
import queue
import random
import threading
import time
import tkinter as tk

import cv2
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .clipboard_utils import clipboard_digest, normalize_clipboard_text
from .execution import ExecutionManager
from .models import AgentDirective, DirectiveKind, ExecutionRequest, ExecutionResult, ExecutionStatus, RiskLevel
from .prompt_builder import build_initial_prompt
from .protocol import ProtocolError, format_result, parse_agent_directive, resolve_cwd
from .redaction import redact_secrets
from .security import classify_command
from .state_machine import AutoState, AutoStateMachine, AutoTransitionError
from .storage import Settings, SettingsStore
from .target_session import TargetSessionResult, TargetSessionRunner, compose_observation_sheet, format_multiple_result
from .test_session import PersistentTestSession, TestSessionResult, TestSessionState, format_test_session_result
from .workspace import WindowWorkspaceManager
from .visual_match import (
    TemplateMatchError,
    best_template_match_exact,
    bgra_bytes_to_bgr,
    find_template_exact,
    load_template,
    write_image,
)
from .visual_watch import VisualStabilityTracker, VisualState
from .win32_input import (
    DesktopAutomationUnavailable,
    ScreenPoint,
    ScreenRect,
    WindowSnapshot,
    Win32DesktopInput,
    enable_per_monitor_dpi_awareness,
)


BG = "#0f131a"
PANEL = "#171d26"
PANEL_2 = "#1e2632"
TEXT = "#e8edf4"
MUTED = "#8f9aaa"
ACCENT = "#6ea8fe"
SUCCESS = "#58d68d"
WARNING = "#f4c95d"
DANGER = "#ff6b6b"
BORDER = "#2c3644"
PURPLE = "#b79cff"


def jittered_action_delay_ms(base_seconds: float, jitter_percent: float, rng=None) -> int:
    """Return a non-negative UI action delay with bounded symmetric jitter.

    Jitter is intentionally reserved for action pacing. Timeouts and visual
    stability thresholds remain deterministic elsewhere in the application.
    """
    base = max(0.0, float(base_seconds))
    jitter = max(0.0, min(50.0, float(jitter_percent))) / 100.0
    if base <= 0.0 or jitter <= 0.0:
        return int(round(base * 1000.0))
    source = rng if rng is not None else random
    factor = 1.0 + float(source.uniform(-jitter, jitter))
    return max(0, int(round(base * factor * 1000.0)))


class AutoSetupDialog(tk.Toplevel):
    """Captures browser click points and the visual response region used by Agent Auto."""

    def __init__(
        self,
        parent: "ClipboardAgentApp",
        desktop: Win32DesktopInput,
        settings: Settings,
        on_saved: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.parent = parent
        self.desktop = desktop
        self.settings = settings
        self.on_saved = on_saved
        self.title("Configuration Agent Auto")
        # Keep the setup usable on laptops / scaled Windows desktops: the
        # content can be scrolled and the dialog can also be resized.
        self.geometry("800x760")
        self.minsize(660, 520)
        self.resizable(True, True)
        self.configure(bg=BG)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close_and_persist)

        self.points: dict[str, ScreenPoint | None] = {
            "prompt": self._point(settings.auto_prompt_x, settings.auto_prompt_y),
            "send": self._point(settings.auto_send_x, settings.auto_send_y),
            "response_tl": self._point(settings.auto_response_left, settings.auto_response_top),
            "response_br": self._point(settings.auto_response_right, settings.auto_response_bottom),
        }
        self.coord_vars: dict[str, tk.StringVar] = {}
        self.countdown_job: str | None = None
        self.stable_var = tk.DoubleVar(value=float(settings.auto_visual_stable_seconds or 3.0))
        self.visual_timeout_var = tk.DoubleVar(value=float(settings.auto_visual_timeout_seconds or 180.0))
        self.clipboard_wait_var = tk.DoubleVar(value=float(settings.auto_clipboard_timeout_seconds or 1.5))
        self.delay_result_to_send_var = tk.DoubleVar(value=float(settings.auto_delay_result_to_send_seconds))
        self.delay_clipboard_to_prompt_var = tk.DoubleVar(value=float(settings.auto_delay_clipboard_to_prompt_seconds))
        self.delay_prompt_to_paste_var = tk.DoubleVar(value=float(settings.auto_delay_prompt_to_paste_seconds))
        self.delay_paste_to_send_var = tk.DoubleVar(value=float(settings.auto_delay_paste_to_send_seconds))
        self.delay_send_to_watch_var = tk.DoubleVar(value=float(settings.auto_delay_send_to_watch_seconds))
        self.delay_stable_to_copy_var = tk.DoubleVar(value=float(settings.auto_delay_stable_to_copy_seconds))
        self.timing_jitter_var = tk.DoubleVar(value=float(settings.auto_timing_jitter_percent))
        self.timing_jitter_label_var = tk.StringVar(value="")
        self.target_window_timeout_var = tk.DoubleVar(value=float(settings.auto_target_window_timeout_seconds))
        self.target_launch_settle_var = tk.DoubleVar(value=float(settings.auto_target_launch_settle_seconds))
        self.target_action_delay_var = tk.DoubleVar(value=float(settings.auto_target_action_delay_seconds))
        self.target_close_timeout_var = tk.DoubleVar(value=float(settings.auto_target_close_timeout_seconds))
        self.target_restore_delay_var = tk.DoubleVar(value=float(settings.auto_target_restore_delay_seconds))
        self.target_attachment_delay_var = tk.DoubleVar(value=float(settings.auto_target_attachment_delay_seconds))
        self.target_attachment_to_send_var = tk.DoubleVar(value=float(settings.auto_target_attachment_to_send_seconds))
        self.test_ready_stable_var = tk.DoubleVar(value=float(settings.auto_test_ready_stable_seconds))
        self.test_ready_poll_var = tk.IntVar(value=int(settings.auto_test_ready_poll_ms))
        self.test_activation_settle_var = tk.DoubleVar(value=float(settings.auto_test_activation_settle_seconds))
        self.copy_template_var = tk.StringVar(value=str(settings.auto_copy_template_path or ""))
        self.copy_threshold_var = tk.DoubleVar(value=float(settings.auto_copy_match_threshold or 0.86))

        self._build_ui()
        self.copy_template_var.trace_add("write", lambda *_args: self._refresh_coords())
        self.timing_jitter_var.trace_add("write", lambda *_args: self._refresh_timing_jitter_label())
        self._refresh_timing_jitter_label()
        self._refresh_coords()

    @staticmethod
    def _point(x: int | None, y: int | None) -> ScreenPoint | None:
        if x is None or y is None:
            return None
        return ScreenPoint(int(x), int(y))

    def _build_ui(self) -> None:
        # The setup grew beyond the height of some displays (notably Windows
        # with display scaling). Put the entire form in a Canvas so every
        # control remains reachable through the scrollbar / mouse wheel.
        shell = ttk.Frame(self)
        shell.pack(fill="both", expand=True)

        self.scroll_canvas = tk.Canvas(
            shell,
            bg=BG,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
        )
        self.scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.scroll_canvas.yview)
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)

        outer = ttk.Frame(self.scroll_canvas, padding=18)
        self._scroll_window = self.scroll_canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", self._on_scroll_content_configure)
        self.scroll_canvas.bind("<Configure>", self._on_scroll_canvas_configure)
        # Toplevel is part of every child widget's bindtags, so the wheel keeps
        # working while the pointer is over labels, entries or buttons.
        self.bind("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind("<Button-4>", self._on_mousewheel, add="+")
        self.bind("<Button-5>", self._on_mousewheel, add="+")

        ttk.Label(outer, text="Configuration Agent Auto", style="Title.TLabel").pack(anchor="w")
        tk.Label(
            outer,
            text=(
                "Configurez les deux clics fixes, la zone visuelle de réponse et l'image de référence du bouton Copier. "
                "Le bouton Copier sera ensuite retrouvé dynamiquement à l'écran. Pour chaque cible fixe, cliquez "
                "« Capturer dans 3 s » puis placez le curseur dessus."
            ),
            bg=BG,
            fg=MUTED,
            justify="left",
            wraplength=670,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(6, 8))
        tk.Label(
            outer,
            text="Chaque capture est enregistrée automatiquement. Fermer cette fenêtre ne perd pas les réglages déjà saisis.",
            bg=BG,
            fg=SUCCESS,
            justify="left",
            wraplength=670,
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w", pady=(0, 14))

        panel = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        panel.pack(fill="x")
        rows = [
            ("prompt", "1. Zone de saisie du prompt"),
            ("send", "2. Bouton Envoyer"),
        ]
        for row, (key, label) in enumerate(rows):
            ttk.Label(panel, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=7)
            var = tk.StringVar(value="non configuré")
            self.coord_vars[key] = var
            ttk.Label(panel, textvariable=var, style="Muted.TLabel").grid(row=row, column=1, sticky="w", padx=12)
            ttk.Button(panel, text="Capturer dans 3 s", command=lambda k=key: self._begin_capture(k)).grid(row=row, column=2, sticky="e", pady=4)
        panel.columnconfigure(0, weight=1)

        visual = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        visual.pack(fill="x", pady=(10, 0))
        ttk.Label(visual, text="3. Zone visuelle — réponse agent", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(
            visual,
            text=(
                "Cadrez uniquement la zone où la réponse assistant change pendant sa génération. "
                "Évitez si possible les vidéos/animations. Cette zone sert uniquement au suivi mouvement/stabilité ; "
                "le bouton Copier est recherché séparément sur l'écran virtuel entier."
            ),
            bg=PANEL,
            fg=MUTED,
            justify="left",
            wraplength=640,
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 8))
        visual_rows = [
            ("response_tl", "Coin haut-gauche"),
            ("response_br", "Coin bas-droit"),
        ]
        for row, (key, label) in enumerate(visual_rows, start=2):
            ttk.Label(visual, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=6)
            var = tk.StringVar(value="non configuré")
            self.coord_vars[key] = var
            ttk.Label(visual, textvariable=var, style="Muted.TLabel").grid(row=row, column=1, sticky="w", padx=12)
            ttk.Button(visual, text="Capturer dans 3 s", command=lambda k=key: self._begin_capture(k)).grid(row=row, column=2, sticky="e", pady=4)
        visual.columnconfigure(0, weight=1)

        copy_match = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        copy_match.pack(fill="x", pady=(10, 0))
        ttk.Label(copy_match, text="4. Détection visuelle du bouton Copier", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(
            copy_match,
            text=(
                "Choisissez une capture de référence exacte du bouton Copier. Le fichier est utilisé tel quel : "
                "aucun redimensionnement n'est appliqué. Après stabilité, OpenCV recherche ce motif à l'échelle 1.00 "
                "sur l'écran virtuel entier (tous les moniteurs)."
            ),
            bg=PANEL,
            fg=MUTED,
            justify="left",
            wraplength=690,
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 8))
        self.copy_template_entry = ttk.Entry(copy_match, textvariable=self.copy_template_var)
        self.copy_template_entry.grid(row=2, column=0, columnspan=2, sticky="ew", pady=4)
        self.copy_template_entry.bind("<FocusOut>", lambda _event: self._persist_draft())
        self.copy_template_entry.bind("<Return>", lambda _event: self._persist_draft())
        ttk.Button(copy_match, text="Parcourir…", command=self._browse_copy_template).grid(row=2, column=2, sticky="e", padx=(8, 0), pady=4)
        ttk.Label(copy_match, text="Seuil de confiance", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(
            copy_match,
            from_=0.50,
            to=0.99,
            increment=0.01,
            width=7,
            textvariable=self.copy_threshold_var,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            buttonbackground=PANEL_2,
            relief="flat",
        ).grid(row=3, column=1, sticky="w", pady=(6, 0))
        ttk.Label(copy_match, text="(0.86 recommandé)", style="Muted.TLabel").grid(row=3, column=2, sticky="w", padx=(8, 0), pady=(6, 0))
        self.test_copy_btn = ttk.Button(
            copy_match,
            text="Tester la détection Copier (déplacer la souris, sans cliquer)",
            command=self._test_copy_detection,
        )
        self.test_copy_btn.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        copy_match.columnconfigure(0, weight=1)
        copy_match.columnconfigure(1, weight=0)

        timings = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        timings.pack(fill="x", pady=(10, 0))
        ttk.Label(timings, text="5. Timings Agent Auto", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Label(
            timings,
            text=(
                "Ces délais espacent les actions de souris/clavier pour laisser le navigateur prendre le focus, "
                "coller et se rafraîchir. La variation aléatoire s'applique uniquement à ces délais d'action, "
                "jamais aux timeouts ni à la durée de stabilité."
            ),
            bg=PANEL,
            fg=MUTED,
            justify="left",
            wraplength=690,
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 10))

        timing_rows = [
            ("Résultat prêt → début du renvoi", self.delay_result_to_send_var, 0.0, 10.0, 0.05),
            ("Message prêt → clic zone prompt", self.delay_clipboard_to_prompt_var, 0.0, 10.0, 0.05),
            ("Clic prompt → collage Ctrl+V", self.delay_prompt_to_paste_var, 0.0, 10.0, 0.05),
            ("Collage → clic Envoyer", self.delay_paste_to_send_var, 0.0, 15.0, 0.05),
            ("Clic Envoyer → surveillance visuelle", self.delay_send_to_watch_var, 0.0, 10.0, 0.05),
            ("Réponse stable → recherche/clic Copier", self.delay_stable_to_copy_var, 0.0, 10.0, 0.05),
        ]
        for row, (label, variable, minimum, maximum, increment) in enumerate(timing_rows, start=2):
            ttk.Label(timings, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=(6, 0))
            tk.Spinbox(
                timings,
                from_=minimum,
                to=maximum,
                increment=increment,
                width=7,
                textvariable=variable,
                bg=PANEL_2,
                fg=TEXT,
                insertbackground=TEXT,
                buttonbackground=PANEL_2,
                relief="flat",
            ).grid(row=row, column=1, padx=(12, 4), pady=(6, 0))
            ttk.Label(timings, text="s", style="Muted.TLabel").grid(row=row, column=2, sticky="w", pady=(6, 0))

        jitter_row = 2 + len(timing_rows)
        ttk.Label(timings, text="Variation aléatoire des délais", style="Panel.TLabel").grid(row=jitter_row, column=0, sticky="w", pady=(10, 0))
        self.timing_jitter_scale = ttk.Scale(
            timings,
            from_=0.0,
            to=50.0,
            orient="horizontal",
            variable=self.timing_jitter_var,
        )
        self.timing_jitter_scale.grid(row=jitter_row, column=1, columnspan=2, sticky="ew", padx=(12, 8), pady=(10, 0))
        ttk.Label(timings, textvariable=self.timing_jitter_label_var, style="Muted.TLabel").grid(row=jitter_row, column=3, sticky="e", pady=(10, 0))

        sep_row = jitter_row + 1
        ttk.Separator(timings, orient="horizontal").grid(row=sep_row, column=0, columnspan=4, sticky="ew", pady=(12, 8))
        ttk.Label(timings, text="Stabilité requise avant Copier", style="Panel.TLabel").grid(row=sep_row + 1, column=0, sticky="w")
        tk.Spinbox(
            timings,
            from_=1.0,
            to=15.0,
            increment=0.5,
            width=7,
            textvariable=self.stable_var,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            buttonbackground=PANEL_2,
            relief="flat",
        ).grid(row=sep_row + 1, column=1, padx=(12, 4))
        ttk.Label(timings, text="s", style="Muted.TLabel").grid(row=sep_row + 1, column=2, sticky="w")

        ttk.Label(timings, text="Timeout maximum de réponse", style="Panel.TLabel").grid(row=sep_row + 2, column=0, sticky="w", pady=(8, 0))
        tk.Spinbox(
            timings,
            from_=30,
            to=900,
            increment=30,
            width=7,
            textvariable=self.visual_timeout_var,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            buttonbackground=PANEL_2,
            relief="flat",
        ).grid(row=sep_row + 2, column=1, padx=(12, 4), pady=(8, 0))
        ttk.Label(timings, text="s", style="Muted.TLabel").grid(row=sep_row + 2, column=2, sticky="w", pady=(8, 0))

        ttk.Label(timings, text="Délai max après clic Copier", style="Panel.TLabel").grid(row=sep_row + 3, column=0, sticky="w", pady=(8, 0))
        tk.Spinbox(
            timings,
            from_=0.5,
            to=10,
            increment=0.5,
            width=7,
            textvariable=self.clipboard_wait_var,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            buttonbackground=PANEL_2,
            relief="flat",
        ).grid(row=sep_row + 3, column=1, padx=(12, 4), pady=(8, 0))
        ttk.Label(timings, text="s", style="Muted.TLabel").grid(row=sep_row + 3, column=2, sticky="w", pady=(8, 0))
        timings.columnconfigure(0, weight=1)
        timings.columnconfigure(1, weight=1)

        target = ttk.Frame(outer, style="Panel.TFrame", padding=12)
        target.pack(fill="x", pady=(10, 0))
        ttk.Label(target, text="6. Target App / TestSession", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(
            target,
            text=(
                "Ces timings concernent #Multiple et la TestSession persistante. "
                "La fenêtre cible passe au premier plan uniquement pendant les actions ; le workspace LLM est restauré avant chaque réponse."
            ),
            bg=PANEL, fg=MUTED, justify="left", wraplength=690, font=("Segoe UI", 9),
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 8))
        target_rows = [
            ("Timeout détection fenêtre cible", self.target_window_timeout_var, 1.0, 180.0, 1.0),
            ("Attente après lancement", self.target_launch_settle_var, 0.0, 30.0, 0.25),
            ("Délai entre actions Target App", self.target_action_delay_var, 0.0, 5.0, 0.05),
            ("Timeout fermeture cible", self.target_close_timeout_var, 0.2, 30.0, 0.25),
            ("Attente après restauration navigateur", self.target_restore_delay_var, 0.0, 5.0, 0.05),
            ("Texte résultat → collage image", self.target_attachment_delay_var, 0.0, 5.0, 0.05),
            ("Image collée → clic Envoyer", self.target_attachment_to_send_var, 0.0, 8.0, 0.05),
            ("Readiness auto : stabilité visuelle", self.test_ready_stable_var, 0.2, 30.0, 0.25),
            ("Readiness : période de poll (ms)", self.test_ready_poll_var, 50, 5000, 50),
            ("Attente après activation Target", self.test_activation_settle_var, 0.0, 15.0, 0.1),
        ]
        for row, (label, variable, minimum, maximum, increment) in enumerate(target_rows, start=2):
            ttk.Label(target, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=(6, 0))
            tk.Spinbox(
                target, from_=minimum, to=maximum, increment=increment, width=7,
                textvariable=variable, bg=PANEL_2, fg=TEXT, insertbackground=TEXT,
                buttonbackground=PANEL_2, relief="flat",
            ).grid(row=row, column=1, padx=(12, 4), pady=(6, 0))
            unit = "ms" if "(ms)" in label else "s"
            ttk.Label(target, text=unit, style="Muted.TLabel").grid(row=row, column=2, sticky="w", pady=(6, 0))
        target.columnconfigure(0, weight=1)

        self.info_var = tk.StringVar(value="")
        tk.Label(outer, textvariable=self.info_var, bg=BG, fg=ACCENT, anchor="w", font=("Segoe UI Semibold", 9)).pack(fill="x", pady=(10, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Fermer", command=self._close_and_persist).pack(side="right")
        self.save_btn = ttk.Button(actions, text="Enregistrer et fermer", style="Accent.TButton", command=self._save)
        self.save_btn.pack(side="right", padx=(0, 8))

    def _on_scroll_content_configure(self, _event: tk.Event | None = None) -> None:
        """Keep the canvas scrollregion synchronized with the whole form."""
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _on_scroll_canvas_configure(self, event: tk.Event) -> None:
        """Stretch the embedded form to the visible canvas width."""
        self.scroll_canvas.itemconfigure(self._scroll_window, width=max(1, int(event.width)))

    def _on_mousewheel(self, event: tk.Event) -> str | None:
        """Scroll the setup form with Windows/macOS wheel or Linux buttons."""
        if not self.winfo_exists():
            return None
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return None
            # Windows usually reports +/-120 per notch; trackpads can emit
            # smaller values, for which one unit still gives useful motion.
            units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
        self.scroll_canvas.yview_scroll(units, "units")
        return "break"

    def _refresh_timing_jitter_label(self) -> None:
        try:
            value = max(0.0, min(50.0, float(self.timing_jitter_var.get())))
        except (ValueError, tk.TclError, TypeError):
            value = 0.0
        self.timing_jitter_label_var.set(f"± {value:.0f} %")

    def _response_rect(self) -> ScreenRect | None:
        a = self.points.get("response_tl")
        b = self.points.get("response_br")
        if not a or not b:
            return None
        return ScreenRect.from_points(a, b)

    def _refresh_coords(self) -> None:
        for key, point in self.points.items():
            self.coord_vars[key].set(f"X={point.x}  Y={point.y}" if point else "non configuré")
        rect = self._response_rect()
        valid_rect = bool(rect and rect.width >= 80 and rect.height >= 40)
        template_path = self.copy_template_var.get().strip()
        if rect and not valid_rect:
            self.info_var.set("La zone Réponse agent doit mesurer au moins 80 × 40 px.")
        self.save_btn.configure(
            state="normal" if all(self.points.values()) and valid_rect and bool(template_path) else "disabled"
        )
        # Copy detection is now independent from the response-motion ROI: it
        # searches the complete virtual screen.  The ROI is still required to
        # save/start Auto because it drives movement/stability tracking.
        self.test_copy_btn.configure(
            state="normal" if bool(template_path) else "disabled"
        )

    def _browse_copy_template(self) -> None:
        initial = self.copy_template_var.get().strip()
        initial_dir = str(Path(initial).expanduser().parent) if initial else str(Path.home())
        path = filedialog.askopenfilename(
            parent=self,
            title="Image de référence du bouton Copier",
            initialdir=initial_dir,
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.bmp *.webp"),
                ("PNG", "*.png"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if not path:
            return
        self.copy_template_var.set(path)
        try:
            image = load_template(path)
        except TemplateMatchError as exc:
            self.info_var.set(str(exc))
        else:
            h, w = image.shape[:2]
            self.info_var.set(f"Image Copier chargée : {w} × {h} px — chemin enregistré.")
        self._persist_draft()
        self._refresh_coords()

    def _test_copy_detection(self) -> None:
        """Diagnose exact-scale Copy matching on the complete visible virtual screen."""
        if self.countdown_job:
            return
        self._persist_draft()

        template_path = self.copy_template_var.get().strip()
        if not template_path:
            messagebox.showerror("Image Copier requise", "Sélectionnez l'image de référence Copier.", parent=self)
            return
        try:
            template = load_template(template_path)
        except TemplateMatchError as exc:
            messagebox.showerror("Image Copier invalide", str(exc), parent=self)
            return

        threshold = self._bounded_float(
            self.copy_threshold_var,
            self.settings.auto_copy_match_threshold or 0.86,
            0.50,
            0.99,
        )
        template_h, template_w = template.shape[:2]
        self.info_var.set(
            f"Test Copier : template réel {template_w}×{template_h}px, échelle 1.00. "
            "L'application va se masquer et capturer l'écran virtuel entier…"
        )
        self.update_idletasks()

        parent_was_visible = self.parent.state() != "withdrawn"
        self.withdraw()
        if parent_was_visible:
            self.parent.withdraw()

        def restore(message: str, *, error: bool = False) -> None:
            if parent_was_visible:
                self.parent.deiconify()
            self.deiconify()
            self.lift()
            self.focus_force()
            self.info_var.set(message)
            self.countdown_job = None
            if error:
                messagebox.showerror("Test Copier", message, parent=self)

        def detect() -> None:
            try:
                geometry, frame = self.desktop.capture_virtual_screen()
                scene = bgra_bytes_to_bgr(frame.pixels, frame.width, frame.height)
                # Diagnostic mode deliberately returns the best candidate even
                # below the configured Auto threshold.  No click is performed.
                match = best_template_match_exact(scene, template)
                screen_x, screen_y = match.screen_center(geometry.x, geometry.y)
                target = ScreenPoint(screen_x, screen_y)
                actual = self.desktop.move_cursor(target, verify=True)

                diag_dir = Path.home() / ".clipboard_agent_relay" / "diagnostics"
                annotated = scene.copy()
                cv2.rectangle(
                    annotated,
                    (match.left, match.top),
                    (match.left + match.width - 1, match.top + match.height - 1),
                    (0, 255, 255),
                    2,
                )
                write_image(diag_dir / "copy_screen.png", scene)
                write_image(diag_dir / "copy_template.png", template)
                write_image(diag_dir / "copy_match.png", annotated)

                verdict = "ACCEPTÉ par Auto" if match.confidence >= threshold else "REFUSÉ par Auto"
                message = (
                    f"Meilleur candidat : {match.confidence:.1%} ({verdict}, seuil {threshold:.1%}) — "
                    f"template {template_w}×{template_h}px à échelle 1.00 ({template_path}) — "
                    f"écran virtuel {geometry.width}×{geometry.height}px origine ({geometry.x},{geometry.y}) — "
                    f"local=({match.center_x},{match.center_y}) → écran=({target.x},{target.y}) "
                    f"→ curseur=({actual.x},{actual.y}). Aucun clic. Diagnostics : {diag_dir}"
                )
            except (DesktopAutomationUnavailable, TemplateMatchError) as exc:
                restore(f"Échec du test Copier : {exc}", error=True)
                return
            self.countdown_job = self.after(2400, lambda: restore(message))

        self.countdown_job = self.after(900, detect)

    def _begin_capture(self, key: str) -> None:
        if self.countdown_job:
            return
        self.info_var.set("Capture dans 3… placez le curseur sur la cible.")
        remaining = 3

        def tick() -> None:
            nonlocal remaining
            if remaining > 0:
                self.info_var.set(f"Capture dans {remaining}… placez le curseur sur la cible.")
                remaining -= 1
                self.countdown_job = self.after(1000, tick)
                return
            self.countdown_job = None
            try:
                point = self.desktop.cursor_position()
            except DesktopAutomationUnavailable as exc:
                messagebox.showerror("Capture impossible", str(exc), parent=self)
                return
            self.points[key] = point
            self._refresh_coords()
            self._persist_draft()
            self.info_var.set(f"Position capturée et enregistrée : X={point.x}, Y={point.y}")

        tick()

    @staticmethod
    def _bounded_float(var: tk.Variable, fallback: float, minimum: float, maximum: float) -> float:
        try:
            value = float(var.get())
        except (ValueError, tk.TclError, TypeError):
            return float(fallback)
        return max(minimum, min(maximum, value))

    def _persist_draft(self) -> None:
        """Persist every captured value, including incomplete setup drafts.

        The previous V2.2 dialog only wrote settings when the explicit Save
        button was pressed. Closing the window therefore discarded all points.
        Here every capture is durable immediately and valid timing fields are
        also flushed whenever the dialog closes.
        """
        mapping = {
            "prompt": ("auto_prompt_x", "auto_prompt_y"),
            "send": ("auto_send_x", "auto_send_y"),
        }
        for key, (x_name, y_name) in mapping.items():
            point = self.points.get(key)
            setattr(self.settings, x_name, point.x if point else None)
            setattr(self.settings, y_name, point.y if point else None)

        # Persist the visual region in normalized rectangle form. Even if the
        # user accidentally captures the two corners in reverse order, the
        # stored configuration remains valid. Partial corners are still kept.
        top_left = self.points.get("response_tl")
        bottom_right = self.points.get("response_br")
        if top_left and bottom_right:
            rect = ScreenRect.from_points(top_left, bottom_right)
            self.settings.auto_response_left = rect.left
            self.settings.auto_response_top = rect.top
            self.settings.auto_response_right = rect.right
            self.settings.auto_response_bottom = rect.bottom
        else:
            self.settings.auto_response_left = top_left.x if top_left else None
            self.settings.auto_response_top = top_left.y if top_left else None
            self.settings.auto_response_right = bottom_right.x if bottom_right else None
            self.settings.auto_response_bottom = bottom_right.y if bottom_right else None

        self.settings.auto_visual_stable_seconds = self._bounded_float(
            self.stable_var, self.settings.auto_visual_stable_seconds or 3.0, 1.0, 15.0
        )
        self.settings.auto_visual_timeout_seconds = self._bounded_float(
            self.visual_timeout_var, self.settings.auto_visual_timeout_seconds or 180.0, 30.0, 900.0
        )
        self.settings.auto_clipboard_timeout_seconds = self._bounded_float(
            self.clipboard_wait_var, self.settings.auto_clipboard_timeout_seconds or 1.5, 0.5, 10.0
        )
        self.settings.auto_delay_result_to_send_seconds = self._bounded_float(
            self.delay_result_to_send_var, self.settings.auto_delay_result_to_send_seconds, 0.0, 10.0
        )
        self.settings.auto_delay_clipboard_to_prompt_seconds = self._bounded_float(
            self.delay_clipboard_to_prompt_var, self.settings.auto_delay_clipboard_to_prompt_seconds, 0.0, 10.0
        )
        self.settings.auto_delay_prompt_to_paste_seconds = self._bounded_float(
            self.delay_prompt_to_paste_var, self.settings.auto_delay_prompt_to_paste_seconds, 0.0, 10.0
        )
        self.settings.auto_delay_paste_to_send_seconds = self._bounded_float(
            self.delay_paste_to_send_var, self.settings.auto_delay_paste_to_send_seconds, 0.0, 15.0
        )
        self.settings.auto_delay_send_to_watch_seconds = self._bounded_float(
            self.delay_send_to_watch_var, self.settings.auto_delay_send_to_watch_seconds, 0.0, 10.0
        )
        self.settings.auto_delay_stable_to_copy_seconds = self._bounded_float(
            self.delay_stable_to_copy_var, self.settings.auto_delay_stable_to_copy_seconds, 0.0, 10.0
        )
        self.settings.auto_timing_jitter_percent = self._bounded_float(
            self.timing_jitter_var, self.settings.auto_timing_jitter_percent, 0.0, 50.0
        )
        self.settings.auto_target_window_timeout_seconds = self._bounded_float(
            self.target_window_timeout_var, self.settings.auto_target_window_timeout_seconds, 1.0, 180.0
        )
        self.settings.auto_target_launch_settle_seconds = self._bounded_float(
            self.target_launch_settle_var, self.settings.auto_target_launch_settle_seconds, 0.0, 30.0
        )
        self.settings.auto_target_action_delay_seconds = self._bounded_float(
            self.target_action_delay_var, self.settings.auto_target_action_delay_seconds, 0.0, 5.0
        )
        self.settings.auto_target_close_timeout_seconds = self._bounded_float(
            self.target_close_timeout_var, self.settings.auto_target_close_timeout_seconds, 0.2, 30.0
        )
        self.settings.auto_target_restore_delay_seconds = self._bounded_float(
            self.target_restore_delay_var, self.settings.auto_target_restore_delay_seconds, 0.0, 5.0
        )
        self.settings.auto_target_attachment_delay_seconds = self._bounded_float(
            self.target_attachment_delay_var, self.settings.auto_target_attachment_delay_seconds, 0.0, 5.0
        )
        self.settings.auto_target_attachment_to_send_seconds = self._bounded_float(
            self.target_attachment_to_send_var, self.settings.auto_target_attachment_to_send_seconds, 0.0, 8.0
        )
        self.settings.auto_test_ready_stable_seconds = self._bounded_float(
            self.test_ready_stable_var, self.settings.auto_test_ready_stable_seconds, 0.2, 30.0
        )
        self.settings.auto_test_ready_poll_ms = int(self._bounded_float(
            self.test_ready_poll_var, self.settings.auto_test_ready_poll_ms, 50, 5000
        ))
        self.settings.auto_test_activation_settle_seconds = self._bounded_float(
            self.test_activation_settle_var, self.settings.auto_test_activation_settle_seconds, 0.0, 15.0
        )
        self.settings.auto_copy_template_path = self.copy_template_var.get().strip()
        self.settings.auto_copy_match_threshold = self._bounded_float(
            self.copy_threshold_var, self.settings.auto_copy_match_threshold or 0.86, 0.50, 0.99
        )
        try:
            geometry = self.desktop.screen_geometry()
        except DesktopAutomationUnavailable:
            geometry = None
        if geometry is not None:
            self.settings.auto_screen_x = geometry.x
            self.settings.auto_screen_y = geometry.y
            self.settings.auto_screen_width = geometry.width
            self.settings.auto_screen_height = geometry.height
        self.parent.store.save(self.settings)

    def _close_and_persist(self) -> None:
        if self.countdown_job:
            try:
                self.after_cancel(self.countdown_job)
            except tk.TclError:
                pass
            self.countdown_job = None
        self._persist_draft()
        self.parent.settings = self.parent.store.load()
        self.parent._update_auto_controls()
        self.destroy()

    def _save(self) -> None:
        # Always persist the draft first. A partial setup remains available next
        # time even if validation below fails.
        self._persist_draft()
        if not all(self.points.values()):
            messagebox.showerror("Configuration incomplète", "Capturez les deux clics et les deux coins de la zone Réponse agent.", parent=self)
            return
        template_path = self.copy_template_var.get().strip()
        if not template_path:
            messagebox.showerror("Image Copier requise", "Sélectionnez une image de référence du bouton Copier.", parent=self)
            return
        try:
            load_template(template_path)
        except TemplateMatchError as exc:
            messagebox.showerror("Image Copier invalide", str(exc), parent=self)
            return
        rect = self._response_rect()
        if not rect or rect.width < 80 or rect.height < 40:
            messagebox.showerror("Zone invalide", "La zone Réponse agent doit mesurer au moins 80 × 40 px.", parent=self)
            return
        try:
            # Validate that this Windows session can actually capture the chosen region.
            self.desktop.capture_signature(rect)
        except DesktopAutomationUnavailable as exc:
            messagebox.showerror("Configuration invalide", str(exc), parent=self)
            return

        self._persist_draft()
        self.destroy()
        self.on_saved()


class ClipboardAgentApp(tk.Tk):
    POLL_MS = 250

    def __init__(self) -> None:
        super().__init__()
        self.title("Clipboard Agent Relay")
        self.geometry("1180x800")
        self.minsize(980, 690)
        self.configure(bg=BG)

        self.store = SettingsStore()
        self.settings = self.store.load()
        self.executor = ExecutionManager()
        self.desktop = Win32DesktopInput()
        self.workspace = WindowWorkspaceManager(self.desktop)
        self.target_runner = TargetSessionRunner(self.desktop, self.executor)
        self.test_session = PersistentTestSession(self.desktop, self.executor, self.workspace)
        self.event_queue: queue.Queue[tuple] = queue.Queue()
        self.user_intervention = threading.Event()
        self._rng = random.Random()

        self.last_seen_clipboard_hash = ""
        self.last_written_clipboard_hash = ""
        self.last_written_clipboard_normalized = ""
        self.pending_request: ExecutionRequest | None = None
        self.pending_cwd: Path | None = None
        self.pending_kind = DirectiveKind.EXECUTION
        self.pending_actions = ()
        self.target_browser_snapshot: WindowSnapshot | None = None
        self.target_interrupted_by_user = False
        self.test_interrupted_by_user = False
        self.last_result_text = ""
        self.last_result_request_id = ""
        self.last_result_kind: DirectiveKind | None = None
        self.last_result_attachment = None
        self.execution_count = 0
        self.processed_agent_hashes: set[str] = set()
        self.processed_request_ids: set[str] = set()

        self.auto_enabled = False
        self.auto_paused = False
        self.auto_machine = AutoStateMachine()
        self.auto_jobs: set[str] = set()
        self.auto_copy_baseline_hash = ""
        self.auto_copy_baseline_sequence: int | None = None
        self.auto_visual_tracker: VisualStabilityTracker | None = None
        self.auto_copy_template = None
        self.auto_pending_attachment = None

        self._build_style()
        self._build_ui()
        self._restore_settings()
        self.after(self.POLL_MS, self._poll_clipboard)
        self.after(80, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_status("EN ATTENTE", "Copiez une réponse contenant #Execution, #Multiple, #Show ou #End depuis le LLM.", MUTED)
        self._update_auto_controls()

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Panel2.TFrame", background=PANEL_2)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 20))
        style.configure("Section.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI Semibold", 11))
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(12, 8), background=PANEL_2, foreground=TEXT)
        style.map("TButton", background=[("active", "#293445")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#09101a")
        style.map("Accent.TButton", background=[("active", "#8abbff")])
        style.configure("Danger.TButton", background="#642f36", foreground=TEXT)
        style.map("Danger.TButton", background=[("active", "#7a3942")])
        style.configure("Auto.TButton", background="#58468a", foreground=TEXT)
        style.map("Auto.TButton", background=[("active", "#7059aa")])
        style.configure("TEntry", fieldbackground=PANEL_2, foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER, padding=8)
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, font=("Segoe UI", 9))
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("Treeview", background=PANEL_2, fieldbackground=PANEL_2, foreground=TEXT, bordercolor=BORDER, rowheight=28)
        style.configure("Treeview.Heading", background=PANEL, foreground=TEXT, font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#2b4f78")])

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="Clipboard Agent Relay", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="LLM Web ⇄ presse-papiers ⇄ machine locale", foreground=MUTED).pack(side="left", padx=16, pady=(7, 0))
        self.auto_badge = tk.Label(header, text="AUTO OFF", bg="#252c37", fg=MUTED, font=("Segoe UI Semibold", 9), padx=10, pady=5)
        self.auto_badge.pack(side="right", pady=(2, 0))

        project = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        project.pack(fill="x", pady=(0, 10))
        ttk.Label(project, text="Projet", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.project_var = tk.StringVar()
        self.project_entry = ttk.Entry(project, textvariable=self.project_var)
        self.project_entry.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(project, text="Choisir…", command=self._choose_project).grid(row=1, column=1, padx=(8, 0), pady=(8, 0))
        ttk.Button(project, text="Nouveau projet", command=self._new_project).grid(row=1, column=2, padx=(8, 0), pady=(8, 0))
        project.columnconfigure(0, weight=1)

        goal_panel = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        goal_panel.pack(fill="x", pady=(0, 10))
        top = ttk.Frame(goal_panel, style="Panel.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="Topic / Destination Goal", style="Section.TLabel").pack(side="left")
        ttk.Label(top, text="objectif long terme rappelé à l'agent", style="Muted.TLabel").pack(side="left", padx=10)
        self.goal_text = tk.Text(goal_panel, height=4, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", wrap="word", font=("Segoe UI", 10), padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        self.goal_text.pack(fill="x", pady=(8, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 10))
        ttk.Button(controls, text="Copier le prompt initial", style="Accent.TButton", command=self._copy_initial_prompt).pack(side="left")
        ttk.Button(controls, text="Recopier le dernier résultat", command=self._recopy_result).pack(side="left", padx=8)
        self.auto_button = ttk.Button(controls, text="Agent Auto", style="Auto.TButton", command=self._toggle_auto)
        self.auto_button.pack(side="left", padx=(4, 6))
        ttk.Button(controls, text="Réglages Auto…", command=lambda: self._open_auto_setup(False)).pack(side="left")
        self.auto_low_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Auto-exécuter faible risque (mode manuel)", variable=self.auto_low_var).pack(side="left", padx=12)

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="Panel.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        right = ttk.Frame(body, style="Panel.TFrame", padding=14)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        self.status_title = tk.Label(left, text="EN ATTENTE", bg=PANEL, fg=MUTED, font=("Segoe UI Semibold", 18), anchor="w")
        self.status_title.pack(fill="x")
        self.status_detail = tk.Label(left, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left")
        self.status_detail.pack(fill="x", pady=(2, 12))

        ttk.Label(left, text="Commande détectée", style="Section.TLabel").pack(anchor="w")
        self.command_text = tk.Text(left, height=7, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", wrap="word", font=("Cascadia Mono", 10), padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER)
        self.command_text.pack(fill="x", pady=(7, 8))
        self.command_text.configure(state="disabled")

        meta = ttk.Frame(left, style="Panel.TFrame")
        meta.pack(fill="x")
        self.meta_label = ttk.Label(meta, text="Aucune commande", style="Muted.TLabel")
        self.meta_label.pack(side="left")
        self.risk_label = tk.Label(meta, text="", bg=PANEL, fg=MUTED, font=("Segoe UI Semibold", 9))
        self.risk_label.pack(side="right")

        actionbar = ttk.Frame(left, style="Panel.TFrame")
        actionbar.pack(fill="x", pady=(10, 12))
        self.run_btn = ttk.Button(actionbar, text="Exécuter", style="Accent.TButton", command=self._run_pending, state="disabled")
        self.run_btn.pack(side="left")
        self.refuse_btn = ttk.Button(actionbar, text="Refuser", command=self._refuse_pending, state="disabled")
        self.refuse_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(actionbar, text="Arrêter", style="Danger.TButton", command=self._stop_execution, state="disabled")
        self.stop_btn.pack(side="left")

        ttk.Label(left, text="Terminal", style="Section.TLabel").pack(anchor="w")
        self.terminal = tk.Text(left, bg="#0b0f14", fg="#d8dee9", insertbackground=TEXT, relief="flat", wrap="none", font=("Cascadia Mono", 9), padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER)
        self.terminal.pack(fill="both", expand=True, pady=(7, 0))
        self.terminal.tag_configure("stderr", foreground="#ff9a9a")
        self.terminal.tag_configure("info", foreground="#8fbfff")
        self.terminal.tag_configure("agent", foreground="#c7b7ff")

        ttk.Label(right, text="Session", style="Section.TLabel").pack(anchor="w")
        self.session_info = ttk.Label(right, text="0 exécution", style="Muted.TLabel")
        self.session_info.pack(anchor="w", pady=(2, 8))

        columns = ("id", "status", "duration")
        self.history = ttk.Treeview(right, columns=columns, show="headings", height=10)
        self.history.heading("id", text="ID")
        self.history.heading("status", text="Statut")
        self.history.heading("duration", text="Durée")
        self.history.column("id", width=130, anchor="w")
        self.history.column("status", width=100, anchor="center")
        self.history.column("duration", width=70, anchor="e")
        self.history.pack(fill="both", expand=True)

        tip = tk.Label(
            right,
            text=(
                "Manuel : copiez la réponse du LLM, puis recollez le résultat.\n\n"
                "Agent Auto Windows : l'app colle/envoie, surveille visuellement la zone Réponse agent puis clique Copier "
                "quand elle est stable. Un mouvement physique de souris met immédiatement l'Auto en pause."
            ),
            bg=PANEL_2,
            fg=MUTED,
            justify="left",
            anchor="nw",
            font=("Segoe UI", 9),
            padx=12,
            pady=12,
            wraplength=340,
        )
        tip.pack(fill="x", pady=(10, 0))

    def _restore_settings(self) -> None:
        self.project_var.set(self.settings.project_root)
        self.goal_text.delete("1.0", "end")
        self.goal_text.insert("1.0", self.settings.goal)
        self.auto_low_var.set(self.settings.auto_run_low_risk)

    def _save_settings(self) -> None:
        self.settings.project_root = self.project_var.get().strip()
        self.settings.goal = self.goal_text.get("1.0", "end").strip()
        self.settings.shell = "powershell" if os.name == "nt" else "bash"
        self.settings.auto_run_low_risk = self.auto_low_var.get()
        self.store.save(self.settings)
        self._update_auto_controls()

    def _choose_project(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.project_var.get() or str(Path.home()))
        if chosen:
            self.project_var.set(chosen)
            self._save_settings()

    def _new_project(self) -> None:
        if self.executor.running or getattr(getattr(self, "target_runner", None), "running", False) or getattr(getattr(self, "test_session", None), "active", False):
            messagebox.showwarning("Action en cours", "Fermez d'abord la commande/Target App/TestSession avant de créer un nouveau projet.")
            return
        if self.auto_enabled:
            self._stop_auto("Nouveau projet : Agent Auto arrêté.", set_status=False)
        if (self.project_var.get().strip() or self.execution_count or self.goal_text.get("1.0", "end").strip()) and not messagebox.askyesno(
            "Nouveau projet",
            "Réinitialiser la session courante ? La configuration des clics Agent Auto sera conservée.",
        ):
            return

        self.project_var.set("")
        self.goal_text.delete("1.0", "end")
        self.pending_request = None
        self.pending_cwd = None
        self.pending_kind = DirectiveKind.EXECUTION
        self.pending_actions = ()
        self.target_browser_snapshot = None
        self.target_interrupted_by_user = False
        self.test_interrupted_by_user = False
        self.last_result_text = ""
        self.last_result_request_id = ""
        self.last_result_kind = None
        self.last_result_attachment = None
        self.execution_count = 0
        self.processed_agent_hashes.clear()
        self.processed_request_ids.clear()
        self.session_info.configure(text="0 exécution")
        for item in self.history.get_children():
            self.history.delete(item)
        self.terminal.delete("1.0", "end")
        self._clear_command_panel()
        self._save_settings()
        self._set_status("NOUVEAU PROJET", "Session réinitialisée. Sélectionnez la nouvelle racine projet.", ACCENT)
        self.after(80, self._choose_project)

    def _project_root(self) -> Path:
        raw = self.project_var.get().strip()
        if not raw:
            raise ProtocolError("Sélectionnez d'abord un dossier projet.")
        root = Path(raw).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ProtocolError("Le dossier projet sélectionné n'existe pas.")
        return root

    def _build_prompt(self) -> str:
        root = self._project_root()
        shell = "powershell" if os.name == "nt" else "bash"
        return build_initial_prompt(root, self.goal_text.get("1.0", "end"), shell=shell, os_name=platform.system())

    def _copy_initial_prompt(self) -> None:
        try:
            prompt = self._build_prompt()
        except ProtocolError as exc:
            messagebox.showerror("Projet requis", str(exc))
            return
        self._save_settings()
        self._write_clipboard(prompt)
        self._set_status("PROMPT PRÊT", "Le prompt initial est dans le presse-papiers. Collez-le dans le LLM.", ACCENT)

    def _read_clipboard(self) -> str | None:
        try:
            value = self.clipboard_get()
            return value if isinstance(value, str) else None
        except tk.TclError:
            return None

    @staticmethod
    def _hash(text: str) -> str:
        return clipboard_digest(text)

    def _write_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

        # Read back what Tk/Windows actually exposes. Windows may normalize LF
        # to CRLF (and clipboard transports may append a terminal NUL). Without
        # this guard, the watcher can mistake our own prompt/result for a new
        # chatbot response and try to parse it as a directive.
        actual = self._read_clipboard() or text
        normalized = normalize_clipboard_text(actual)
        digest = self._hash(actual)
        self.last_written_clipboard_normalized = normalized
        self.last_written_clipboard_hash = digest
        self.last_seen_clipboard_hash = digest

    def _poll_clipboard(self) -> None:
        try:
            text = self._read_clipboard()
            waiting_auto_copy = (
                self.auto_enabled
                and not self.auto_paused
                and self.auto_state in {
                    AutoState.WAITING_INITIAL_CLIPBOARD,
                    AutoState.WAITING_CLIPBOARD,
                }
            )
            sequence_changed = False
            if waiting_auto_copy and self.auto_copy_baseline_sequence is not None:
                try:
                    current_sequence = self.desktop.clipboard_sequence_number()
                except Exception:
                    current_sequence = None
                sequence_changed = (
                    current_sequence is not None
                    and current_sequence != self.auto_copy_baseline_sequence
                )

            if text:
                normalized = normalize_clipboard_text(text)
                digest = self._hash(text)
                digest_changed = digest != self.last_seen_clipboard_hash

                # In Agent Auto, GetClipboardSequenceNumber is authoritative when
                # available. It lets us detect a real browser Copy event even if
                # the copied text is byte-for-byte identical to what was already
                # in the clipboard. Hash comparison remains the portable fallback.
                copy_observed = waiting_auto_copy and (
                    sequence_changed or digest != self.auto_copy_baseline_hash
                )

                if digest_changed:
                    self.last_seen_clipboard_hash = digest

                if copy_observed:
                    self._cancel_auto_jobs()
                    self._auto_accept_copied_text(text)
                elif digest_changed:
                    # Explicit self-write suppression. This is intentionally
                    # content-based rather than timing-based so a slow Windows
                    # clipboard round-trip cannot trigger a false directive.
                    is_internal_write = normalized == self.last_written_clipboard_normalized
                    if not is_internal_write:
                        if self.auto_enabled:
                            # Ignore unrelated clipboard changes while Auto is
                            # active unless we are explicitly waiting for Copy.
                            pass
                        elif digest != self.last_written_clipboard_hash:
                            self._handle_agent_text(text, source_auto=False)
            self.after(self.POLL_MS, self._poll_clipboard)
        except tk.TclError:
            return

    def _handle_agent_text(self, text: str, *, source_auto: bool) -> None:
        if self.executor.running or getattr(getattr(self, "target_runner", None), "running", False) or getattr(getattr(self, "test_session", None), "busy", False):
            if source_auto:
                self._stop_auto("Réponse reçue alors qu'une action locale est encore en cours.")
            return

        digest = self._hash(text)
        try:
            directive = parse_agent_directive(text, default_shell="powershell" if os.name == "nt" else "bash")
            if directive is None:
                if source_auto:
                    self._stop_auto("Le texte copié ne contient aucune directive reconnue (#Execution, #OpenTestSession, #TestActions, #CloseTestSession, #Multiple, #Show, #End).")
                return
        except ProtocolError as exc:
            if source_auto:
                self._stop_auto(f"Réponse agent invalide : {exc}")
            self._set_status("ERREUR DE PROTOCOLE", str(exc), DANGER)
            self._append_terminal(f"[protocol] {exc}\n", "stderr")
            return

        directive_id = directive.request.request_id if directive.request is not None else directive.request_id
        duplicate_hash = digest in self.processed_agent_hashes
        duplicate_id = bool(directive_id and directive_id in self.processed_request_ids)
        if duplicate_hash or duplicate_id:
            if source_auto and self.auto_state == AutoState.PROCESSING_INITIAL_REPLY:
                if self._auto_resume_from_last_result(directive):
                    return
            if source_auto:
                if duplicate_id and directive_id:
                    self._stop_auto(
                        f"ID de directive déjà traité : {directive_id}, mais aucun résultat local correspondant n'est récupérable."
                    )
                else:
                    self._stop_auto("Le bouton Copier a renvoyé une réponse agent déjà traitée. Vérifiez sa position.")
            else:
                if duplicate_id and directive_id:
                    self._set_status("COMMANDE DÉJÀ TRAITÉE", f"ID {directive_id} ignoré.", WARNING)
                else:
                    self._set_status("RÉPONSE DÉJÀ TRAITÉE", "Cette réponse agent a déjà été exécutée dans cette session.", WARNING)
            return

        self.processed_agent_hashes.add(digest)
        if directive_id:
            self.processed_request_ids.add(directive_id)

        if directive.kind == DirectiveKind.END:
            self._handle_end(directive, source_auto)
            return
        if directive.kind == DirectiveKind.CLOSE_TEST_SESSION:
            self._handle_close_test_session(directive, source_auto)
            return
        if directive.kind == DirectiveKind.TEST_ACTIONS:
            self._handle_test_actions(directive, source_auto)
            return
        if directive.request is None:
            if source_auto:
                self._stop_auto("Directive reçue sans commande exploitable.")
            return

        try:
            root = self._project_root()
            cwd = resolve_cwd(root, directive.request.cwd)
        except ProtocolError as exc:
            if source_auto:
                self._stop_auto(f"Commande invalide : {exc}")
            self._set_status("ERREUR DE PROTOCOLE", str(exc), DANGER)
            self._append_terminal(f"[protocol] {exc}\n", "stderr")
            return

        if directive.kind == DirectiveKind.SHOW:
            self._handle_show(directive.request, cwd, source_auto)
        elif directive.kind == DirectiveKind.MULTIPLE:
            self._handle_multiple(directive, cwd, source_auto)
        elif directive.kind == DirectiveKind.OPEN_TEST_SESSION:
            self._handle_open_test_session(directive, cwd, source_auto)
        else:
            self._handle_execution(directive.request, cwd, source_auto)

    def _auto_resume_from_last_result(self, directive: AgentDirective) -> bool:
        """Resume Auto from an already-processed initial directive/result.

        This is valid only for the reply already visible at Auto start. The
        exact directive ID and kind must match the latest local result, so no
        shell command, temporary #Multiple or persistent TestSession action is
        ever replayed implicitly.
        """
        resumable = {
            DirectiveKind.EXECUTION, DirectiveKind.MULTIPLE,
            DirectiveKind.OPEN_TEST_SESSION, DirectiveKind.TEST_ACTIONS,
            DirectiveKind.CLOSE_TEST_SESSION,
        }
        if directive.kind not in resumable:
            return False
        directive_id = directive.request.request_id if directive.request is not None else directive.request_id
        if not directive_id or not self.last_result_text or not self.last_result_request_id:
            return False
        if directive_id != self.last_result_request_id:
            return False
        last_kind = getattr(self, "last_result_kind", DirectiveKind.EXECUTION)
        if last_kind is not None and directive.kind != last_kind:
            return False
        if not self._transition_auto(AutoState.RECOVERING_LAST_RESULT):
            return True

        result_text = self.last_result_text
        attachment = getattr(self, "last_result_attachment", None)
        self._append_terminal(
            f"[auto-resume] Directive {directive_id} déjà traitée : renvoi du dernier résultat sans réexécution.\n",
            "info",
        )
        delay_ms = self._auto_action_delay_ms("auto_delay_result_to_send_seconds")
        self._set_status(
            "AUTO ACTIF",
            f"Directive initiale {directive_id} déjà exécutée. Renvoi du résultat existant dans {delay_ms / 1000:.2f} s…",
            PURPLE,
        )
        self._schedule_auto(
            delay_ms,
            (lambda text=result_text: self._auto_send_message(text))
            if attachment is None
            else (lambda text=result_text, image=attachment: self._auto_send_message(text, image)),
        )
        return True

    def _handle_execution(self, request: ExecutionRequest, cwd: Path, source_auto: bool) -> None:
        decision = classify_command(request.command)
        self.pending_request = request
        self.pending_cwd = cwd
        self.pending_kind = DirectiveKind.EXECUTION
        self._show_command(request, decision.risk, decision.reason, "EXEC")

        if decision.risk == RiskLevel.BLOCKED:
            if source_auto:
                self._stop_auto(f"Commande bloquée : {decision.reason}", set_status=False)
            self._set_status("COMMANDE BLOQUÉE", decision.reason, DANGER)
            self.run_btn.configure(state="disabled")
            self.refuse_btn.configure(state="normal")
            return

        if decision.risk == RiskLevel.SENSITIVE:
            if source_auto:
                self._pause_auto(f"Commande sensible : {decision.reason}")
            self._set_status("VALIDATION REQUISE", decision.reason, WARNING)
            self.run_btn.configure(state="normal")
            self.refuse_btn.configure(state="normal")
            return

        auto_can_run = source_auto and self.auto_enabled and not self.auto_paused and decision.risk in {RiskLevel.LOW, RiskLevel.MODIFY}
        manual_can_run = not source_auto and decision.risk == RiskLevel.LOW and self.auto_low_var.get()
        if auto_can_run or manual_can_run:
            detail = "Agent Auto : exécution autorisée." if auto_can_run else "Faible risque : exécution automatique."
            self._set_status("COMMANDE DÉTECTÉE", detail, SUCCESS)
            if auto_can_run:
                # Use the guarded Auto scheduler so a physical mouse movement
                # before process start cancels this pending execution.
                self._schedule_auto(100, self._run_pending)
            else:
                self.after(100, self._run_pending)
        else:
            self._set_status("VALIDATION REQUISE", decision.reason, WARNING)
            self.run_btn.configure(state="normal")
            self.refuse_btn.configure(state="normal")

    def _handle_multiple(self, directive: AgentDirective, cwd: Path, source_auto: bool) -> None:
        test_session = getattr(self, "test_session", None)
        if test_session is not None and test_session.active:
            if source_auto:
                self._stop_auto("#Multiple avec Launch est une session temporaire et ne peut pas démarrer pendant une TestSession persistante. Utilisez #TestActions/#Multiple sans Launch ou #CloseTestSession.")
            else:
                self._set_status("TESTSESSION ACTIVE", "Fermez d'abord la TestSession persistante.", WARNING)
            return
        request = directive.request
        if request is None:
            if source_auto:
                self._stop_auto("#Multiple reçu sans commande de lancement.")
            return
        decision = classify_command(request.command)
        self.pending_request = request
        self.pending_cwd = cwd
        self.pending_kind = DirectiveKind.MULTIPLE
        self.pending_actions = directive.actions
        self._show_multiple_command(request, directive.actions, decision.risk, decision.reason)

        if decision.risk == RiskLevel.BLOCKED:
            if source_auto:
                self._stop_auto(f"#Multiple bloqué : {decision.reason}", set_status=False)
            self._set_status("MULTIPLE BLOQUÉ", decision.reason, DANGER)
            self.run_btn.configure(state="disabled")
            self.refuse_btn.configure(state="normal")
            return
        if decision.risk == RiskLevel.SENSITIVE:
            if source_auto:
                self._pause_auto(f"#Multiple sensible : {decision.reason}")
            self._set_status("VALIDATION REQUISE", f"#Multiple sensible : {decision.reason}", WARNING)
            self.run_btn.configure(state="normal")
            self.refuse_btn.configure(state="normal")
            return

        if source_auto and self.auto_enabled and not self.auto_paused:
            self._set_status("TARGET APP", f"Séquence #Multiple autorisée ({len(directive.actions)} actions).", PURPLE)
            self._schedule_auto(100, self._run_pending)
        else:
            # In manual mode UI injection is intentionally explicit even when
            # the launch command itself is low-risk.
            self._set_status("VALIDATION REQUISE", f"#Multiple : {len(directive.actions)} actions sur l'application cible.", WARNING)
            self.run_btn.configure(state="normal")
            self.refuse_btn.configure(state="normal")

    def _handle_open_test_session(self, directive: AgentDirective, cwd: Path, source_auto: bool) -> None:
        request = directive.request
        if request is None:
            if source_auto:
                self._stop_auto("#OpenTestSession reçu sans commande Launch.")
            return
        if not source_auto:
            self._set_status("TESTSESSION", "#OpenTestSession est disponible en Agent Auto afin de garantir le workspace/focus.", WARNING)
            return
        if self.test_session.active:
            self._stop_auto("Une TestSession est déjà ouverte. Utilisez #TestActions ou #CloseTestSession.")
            return
        decision = classify_command(request.command)
        self._show_multiple_command(
            request,
            directive.actions,
            decision.risk,
            f"Ready={directive.ready} • {decision.reason}",
            mode="TESTSESSION OPEN",
        )
        if decision.risk == RiskLevel.BLOCKED:
            self._stop_auto(f"#OpenTestSession bloqué : {decision.reason}")
            return
        if decision.risk == RiskLevel.SENSITIVE:
            self._pause_auto(f"#OpenTestSession sensible : {decision.reason}")
            return
        if not self._transition_auto(AutoState.TEST_OPENING):
            return
        self._set_status("TESTSESSION", f"Ouverture persistante — readiness {directive.ready}…", PURPLE)
        self._schedule_auto(100, lambda d=directive, c=cwd: self._start_open_test_session(d, c))

    def _start_open_test_session(self, directive: AgentDirective, cwd: Path) -> None:
        if not self.auto_enabled or self.auto_paused or self.user_intervention.is_set():
            return
        request = directive.request
        if request is None:
            self._stop_auto("#OpenTestSession sans commande de lancement.")
            return
        self.test_interrupted_by_user = False
        try:
            self.test_session.open_async(
                request,
                cwd,
                tuple(directive.actions),
                directive.ready,
                window_timeout_seconds=float(self.settings.auto_target_window_timeout_seconds),
                visual_stable_seconds=float(self.settings.auto_test_ready_stable_seconds),
                visual_poll_ms=int(self.settings.auto_test_ready_poll_ms),
                settle_seconds=float(self.settings.auto_test_activation_settle_seconds),
                action_delay_seconds=float(self.settings.auto_target_action_delay_seconds),
                on_status=lambda text: self.event_queue.put(("test_status", text)),
                on_done=lambda result: self.event_queue.put(("test_done", result)),
                on_stage=lambda stage: self.event_queue.put(("test_stage", stage)),
            )
        except Exception as exc:
            self._stop_auto(f"Impossible d'ouvrir la TestSession : {exc}")

    def _handle_test_actions(self, directive: AgentDirective, source_auto: bool) -> None:
        if not source_auto:
            self._set_status("TESTSESSION", "Les actions de TestSession nécessitent Agent Auto pour garantir le focus de la cible.", WARNING)
            return
        if self.test_session.state != TestSessionState.ACTIVE_BACKGROUND:
            self._stop_auto("Aucune TestSession active en arrière-plan. Utilisez d'abord #OpenTestSession.")
            return
        if not directive.actions:
            self._stop_auto("#TestActions ne contient aucune action.")
            return
        if not self._transition_auto(AutoState.TEST_ACTING):
            return
        self.test_interrupted_by_user = False
        request_id = directive.request_id or f"actions-{int(time.time())}"
        self._set_status("TESTSESSION", f"Exécution de {len(directive.actions)} action(s) sur la cible persistante…", PURPLE)
        try:
            self.test_session.actions_async(
                request_id,
                tuple(directive.actions),
                timeout_seconds=max(30.0, float(self.settings.auto_visual_timeout_seconds)),
                action_delay_seconds=float(self.settings.auto_target_action_delay_seconds),
                settle_seconds=float(self.settings.auto_test_activation_settle_seconds),
                on_status=lambda text: self.event_queue.put(("test_status", text)),
                on_done=lambda result: self.event_queue.put(("test_done", result)),
                on_stage=lambda stage: self.event_queue.put(("test_stage", stage)),
            )
        except Exception as exc:
            self._stop_auto(f"Impossible d'exécuter les actions TestSession : {exc}")

    def _handle_close_test_session(self, directive: AgentDirective, source_auto: bool) -> None:
        if not source_auto:
            self._set_status("TESTSESSION", "#CloseTestSession est géré en Agent Auto.", WARNING)
            return
        if self.test_session.state not in {TestSessionState.ACTIVE_BACKGROUND, TestSessionState.ACTIVE_FOREGROUND, TestSessionState.LOST}:
            self._stop_auto("Aucune TestSession ouverte à fermer.")
            return
        if not self._transition_auto(AutoState.TEST_CLOSING):
            return
        self.test_interrupted_by_user = False
        self._set_status("TESTSESSION", "Fermeture de la TestSession persistante…", PURPLE)
        try:
            self.test_session.close_async(
                directive.request_id or f"close-{int(time.time())}",
                close_timeout_seconds=float(self.settings.auto_target_close_timeout_seconds),
                restore_delay_seconds=float(self.settings.auto_target_restore_delay_seconds),
                on_status=lambda text: self.event_queue.put(("test_status", text)),
                on_done=lambda result: self.event_queue.put(("test_done", result)),
                on_stage=lambda stage: self.event_queue.put(("test_stage", stage)),
            )
        except Exception as exc:
            self._stop_auto(f"Impossible de fermer la TestSession : {exc}")

    def _handle_show(self, request: ExecutionRequest, cwd: Path, source_auto: bool) -> None:
        test_session = getattr(self, "test_session", None)
        if test_session is not None and test_session.active:
            if source_auto:
                self._stop_auto("#Show refusé pendant une TestSession persistante. Utilisez #CloseTestSession avant #Show.")
            else:
                self._set_status("TESTSESSION ACTIVE", "Fermez d'abord la TestSession persistante avant #Show.", WARNING)
            return
        decision = classify_command(request.command)
        if source_auto and self.auto_enabled:
            self._stop_auto("#Show reçu : Auto arrêté avant ouverture de la démonstration.", set_status=False)
        self.pending_request = request
        self.pending_cwd = cwd
        self.pending_kind = DirectiveKind.SHOW
        self._show_command(request, decision.risk, decision.reason, "SHOW")

        if decision.risk == RiskLevel.BLOCKED:
            self._set_status("SHOW BLOQUÉ", decision.reason, DANGER)
            self.run_btn.configure(state="disabled")
            self.refuse_btn.configure(state="normal")
            return
        if decision.risk == RiskLevel.SENSITIVE:
            self._set_status("VALIDATION REQUISE", f"#Show sensible : {decision.reason}", WARNING)
            self.run_btn.configure(state="normal")
            self.refuse_btn.configure(state="normal")
            return
        self.after(100, self._run_pending)

    def _handle_end(self, directive: AgentDirective, source_auto: bool) -> None:
        test_session = getattr(self, "test_session", None)
        if test_session is not None and test_session.active:
            test_session.force_close()
        if source_auto and self.auto_enabled:
            self._stop_auto("#End reçu.", set_status=False)
        if directive.summary:
            self._append_terminal("\n[agent] Mission terminée :\n" + directive.summary.strip() + "\n", "agent")
        self._set_status("AGENT TERMINÉ", "#End reçu : le mode automatique est arrêté et la mission est déclarée terminée.", SUCCESS)

    def _show_command(self, request: ExecutionRequest, risk: RiskLevel, reason: str, mode: str) -> None:
        self.command_text.configure(state="normal")
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", request.command)
        self.command_text.configure(state="disabled")
        self.meta_label.configure(text=f"{mode}  •  ID {request.request_id}  •  CWD {request.cwd}  •  timeout {request.timeout}s")
        risk_color = {RiskLevel.LOW: SUCCESS, RiskLevel.MODIFY: WARNING, RiskLevel.SENSITIVE: DANGER, RiskLevel.BLOCKED: DANGER}[risk]
        self.risk_label.configure(text=f"{risk.value} — {reason}", fg=risk_color)

    def _show_multiple_command(
        self,
        request: ExecutionRequest,
        actions,
        risk: RiskLevel,
        reason: str,
        mode: str = "MULTIPLE",
    ) -> None:
        lines = [f"Launch: {request.command}", ""]
        for action in actions:
            if action.kind.value == "CLICK":
                lines.append(f"#Click {action.x};{action.y}")
            elif action.kind.value == "TYPE_INPUT":
                preview = action.text.replace("\n", "\\n")
                lines.append(f"#TypeInput {preview[:120]!r}")
            elif action.kind.value == "KEY":
                lines.append(f"#Key {action.key}")
            elif action.kind.value == "WAIT":
                lines.append(f"#Wait {action.wait_ms}")
            else:
                lines.append(f"#Observe {action.label}".rstrip())
        self.command_text.configure(state="normal")
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", "\n".join(lines))
        self.command_text.configure(state="disabled")
        self.meta_label.configure(text=f"{mode}  •  ID {request.request_id}  •  {len(actions)} actions  •  timeout {request.timeout}s")
        risk_color = {RiskLevel.LOW: SUCCESS, RiskLevel.MODIFY: WARNING, RiskLevel.SENSITIVE: DANGER, RiskLevel.BLOCKED: DANGER}[risk]
        self.risk_label.configure(text=f"{risk.value} — {reason}", fg=risk_color)

    def _clear_command_panel(self) -> None:
        self.command_text.configure(state="normal")
        self.command_text.delete("1.0", "end")
        self.command_text.configure(state="disabled")
        self.meta_label.configure(text="Aucune commande")
        self.risk_label.configure(text="")
        self.run_btn.configure(state="disabled")
        self.refuse_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")

    def _run_pending(self) -> None:
        if not self.pending_request or not self.pending_cwd or self.executor.running:
            return
        if self.pending_kind == DirectiveKind.SHOW:
            self._launch_show_pending()
            return
        if self.pending_kind == DirectiveKind.MULTIPLE:
            self._launch_multiple_pending()
            return

        req = self.pending_request
        cwd = self.pending_cwd
        self.run_btn.configure(state="disabled")
        self.refuse_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._set_status("EXÉCUTION", f"{req.request_id} est en cours…", ACCENT)
        self._append_terminal(f"\n$ {req.command}\n", "info")
        try:
            if self.auto_enabled and not self.auto_paused:
                if not self._transition_auto(AutoState.EXECUTING):
                    return
            self.executor.execute_async(req, cwd, self._thread_output, self._thread_done)
        except Exception as exc:
            self._set_status("ERREUR", str(exc), DANGER)
            self.stop_btn.configure(state="disabled")
            if self.auto_enabled:
                self._stop_auto(f"Impossible de démarrer la commande : {exc}")

    def _launch_multiple_pending(self) -> None:
        req = self.pending_request
        cwd = self.pending_cwd
        actions = tuple(self.pending_actions)
        if not req or not cwd or not actions or self.target_runner.running:
            return

        source_auto = self.auto_enabled and not self.auto_paused
        self.target_interrupted_by_user = False
        browser_snapshot = None
        try:
            if source_auto and self.workspace.binding is not None:
                browser_snapshot = self.workspace.binding.llm
                if self.workspace.relay_hwnd:
                    self.desktop.set_window_topmost(self.workspace.relay_hwnd, False)
            elif self.settings.auto_configured:
                browser_snapshot = self.desktop.snapshot_window_at_point(self._auto_point("prompt"))
            elif self.desktop.available:
                browser_snapshot = self.desktop.snapshot_foreground_window()
        except DesktopAutomationUnavailable as exc:
            if source_auto:
                self._stop_auto(f"Impossible de mémoriser le navigateur avant #Multiple : {exc}")
                return

        if source_auto and browser_snapshot is None:
            self._stop_auto("Impossible d'identifier la fenêtre navigateur contenant la zone de prompt avant #Multiple.")
            return

        self.target_browser_snapshot = browser_snapshot
        self.run_btn.configure(state="disabled")
        self.refuse_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._append_terminal(f"\n[target] Launch: {req.command}\n", "info")
        if source_auto:
            if not self._transition_auto(AutoState.TARGET_STARTING):
                return

        try:
            self.target_runner.start_async(
                req, cwd, actions, browser_snapshot,
                window_timeout_seconds=float(self.settings.auto_target_window_timeout_seconds),
                launch_settle_seconds=float(self.settings.auto_target_launch_settle_seconds),
                action_delay_seconds=float(self.settings.auto_target_action_delay_seconds),
                close_timeout_seconds=float(self.settings.auto_target_close_timeout_seconds),
                restore_delay_seconds=float(self.settings.auto_target_restore_delay_seconds),
                on_status=lambda text: self.event_queue.put(("target_status", text)),
                on_done=lambda result: self.event_queue.put(("target_done", result)),
                on_stage=lambda stage: self.event_queue.put(("target_stage", stage)),
            )
        except Exception as exc:
            self.stop_btn.configure(state="disabled")
            if source_auto:
                self._stop_auto(f"Impossible de démarrer la session Target App : {exc}")
            self._set_status("ERREUR TARGET APP", str(exc), DANGER)
            return

        self._set_status("TARGET APP", "Application cible lancée : attente de sa fenêtre…", PURPLE if source_auto else ACCENT)

    def _launch_show_pending(self) -> None:
        req = self.pending_request
        cwd = self.pending_cwd
        if not req or not cwd:
            return
        self.run_btn.configure(state="disabled")
        self.refuse_btn.configure(state="disabled")
        try:
            pid = self.executor.launch_external(req, cwd)
        except Exception as exc:
            self._set_status("ERREUR SHOW", str(exc), DANGER)
            self._append_terminal(f"[show] {exc}\n", "stderr")
            return
        self.history.insert("", 0, values=(req.request_id, "SHOW", "—"))
        self._append_terminal(f"\n[show] Processus externe lancé (PID {pid}) : {req.command}\n", "info")
        self.pending_request = None
        self.pending_cwd = None
        self.pending_kind = DirectiveKind.EXECUTION
        self.pending_actions = ()
        self._set_status("SHOW LANCÉ", f"Programme lancé dans une console/fenêtre externe (PID {pid}). Agent Auto arrêté.", SUCCESS)

    def _thread_output(self, channel: str, text: str) -> None:
        self.event_queue.put(("output", channel, text))

    def _thread_done(self, result: ExecutionResult) -> None:
        self.event_queue.put(("done", result))

    def _physical_mouse_event(self, x: int, y: int) -> None:
        # Set immediately from the hook thread so scheduled UI actions can see
        # the intervention before Tk has drained the event queue.
        self.user_intervention.set()
        self.event_queue.put(("user_mouse", x, y))

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.event_queue.get_nowait()
                if event[0] == "output":
                    _, channel, text = event
                    self._append_terminal(text, "stderr" if channel == "stderr" else None)
                elif event[0] == "done":
                    self._finalize_result(event[1])
                elif event[0] == "target_status":
                    _, detail = event
                    self._append_terminal(f"[target] {detail}\n", "info")
                    if self.auto_enabled and not self.auto_paused:
                        self._set_status("TARGET APP", detail, PURPLE)
                elif event[0] == "target_stage":
                    _, stage = event
                    if self.auto_enabled and not self.auto_paused:
                        if stage == "running" and self.auto_state == AutoState.TARGET_STARTING:
                            self._transition_auto(AutoState.TARGET_RUNNING)
                        elif stage == "restoring" and self.auto_state in {
                            AutoState.TARGET_STARTING,
                            AutoState.TARGET_RUNNING,
                        }:
                            self._transition_auto(AutoState.TARGET_RESTORING)
                elif event[0] == "target_done":
                    self._finalize_target_result(event[1])
                elif event[0] == "test_status":
                    _, detail = event
                    self._append_terminal(f"[test-session] {detail}\n", "info")
                    if self.auto_enabled and not self.auto_paused:
                        self._set_status("TESTSESSION", detail, PURPLE)
                elif event[0] == "test_stage":
                    _, stage = event
                    if self.auto_enabled and not self.auto_paused and stage == "restoring":
                        if self.auto_state in {AutoState.TEST_OPENING, AutoState.TEST_ACTING}:
                            self._transition_auto(AutoState.TEST_RESTORING)
                elif event[0] == "test_done":
                    self._finalize_test_session_result(event[1])
                elif event[0] == "user_mouse":
                    _, x, y = event
                    if self.auto_enabled and not self.auto_paused:
                        self._pause_auto(f"Mouvement souris utilisateur détecté à ({x}, {y}).")
        except queue.Empty:
            pass
        self.after(80, self._drain_events)

    def _finalize_result(self, result: ExecutionResult) -> None:
        self.stop_btn.configure(state="disabled")
        self.pending_request = None
        self.pending_cwd = None
        self.pending_kind = DirectiveKind.EXECUTION
        self.pending_actions = ()
        self.execution_count += 1
        self.session_info.configure(text=f"{self.execution_count} exécution{'s' if self.execution_count > 1 else ''}")
        self.history.insert("", 0, values=(result.request_id, result.status.value, f"{result.duration:.2f}s"))

        reminder = ""
        every = max(1, int(self.settings.goal_reminder_every or 4))
        if self.execution_count % every == 0:
            reminder = self.goal_text.get("1.0", "end").strip()

        safe_result = ExecutionResult(
            request_id=result.request_id,
            status=result.status,
            exit_code=result.exit_code,
            duration=result.duration,
            cwd=result.cwd,
            stdout=redact_secrets(result.stdout),
            stderr=redact_secrets(result.stderr),
            command=result.command,
            note=result.note,
        )
        text = format_result(safe_result, goal_reminder=reminder, max_output_chars=self.settings.max_output_chars)
        self.last_result_text = text
        self.last_result_request_id = result.request_id
        self.last_result_kind = DirectiveKind.EXECUTION
        self.last_result_attachment = None
        self._write_clipboard(text)

        if self.auto_enabled and not self.auto_paused and not self.user_intervention.is_set():
            if not self._transition_auto(AutoState.SENDING):
                return
            self._set_status("AUTO ACTIF", "Résultat prêt : renvoi automatique au chatbot…", PURPLE)
            self._schedule_auto_action("auto_delay_result_to_send_seconds", lambda: self._auto_send_message(text))
            return

        if result.status == ExecutionStatus.SUCCESS:
            color = SUCCESS
            detail = "Résultat copié. Revenez dans le chat et faites Ctrl+V puis Envoyer."
        else:
            color = WARNING if result.status in {ExecutionStatus.ERROR, ExecutionStatus.TIMEOUT, ExecutionStatus.CANCELLED} else DANGER
            detail = f"{result.status.value} copié dans le presse-papiers. Collez-le au LLM pour qu'il s'adapte."
        if self.auto_paused:
            detail += " Agent Auto est PAUSÉ suite à l'intervention utilisateur."
        self._set_status("RÉSULTAT PRÊT", detail, color)

    def _finalize_target_result(self, result: TargetSessionResult) -> None:
        self.stop_btn.configure(state="disabled")
        self.pending_request = None
        self.pending_cwd = None
        self.pending_kind = DirectiveKind.EXECUTION
        self.pending_actions = ()
        self.execution_count += 1
        self.session_info.configure(text=f"{self.execution_count} exécution{'s' if self.execution_count > 1 else ''}")
        self.history.insert("", 0, values=(result.request_id, f"MULTI/{result.status.value}", f"{result.duration:.2f}s"))
        for line in result.logs:
            self._append_terminal(f"[target] {line}\n", "stderr" if line.startswith("ERROR") else "info")

        reminder = ""
        every = max(1, int(self.settings.goal_reminder_every or 4))
        if self.execution_count % every == 0:
            reminder = self.goal_text.get("1.0", "end").strip()
        text = format_multiple_result(result, goal_reminder=reminder)
        sheet = compose_observation_sheet(result.observations)
        self.last_result_text = text
        self.last_result_request_id = result.request_id
        self.last_result_kind = DirectiveKind.MULTIPLE
        self.last_result_attachment = sheet

        # Physical user intervention owns the desktop immediately. The sticky
        # target_interrupted_by_user flag deliberately survives _stop_auto(),
        # which clears the generic mouse event flag. This prevents a late
        # target_done event from changing the clipboard after the user took over.
        interrupted_by_user = bool(getattr(self, "target_interrupted_by_user", False) or self.user_intervention.is_set())
        self.target_interrupted_by_user = False
        if interrupted_by_user:
            self._set_status(
                "AUTO PAUSÉ" if self.auto_paused else "TARGET APP ARRÊTÉE",
                "Intervention utilisateur pendant Target App : aucun retour automatique vers le navigateur ou le presse-papiers n'est effectué.",
                WARNING,
            )
            return

        if self.auto_enabled and not self.auto_paused and not self.user_intervention.is_set():
            # Normal worker flow emits the restoring stage before target_done.
            # The fallback transition keeps this fail-safe if an older/custom
            # runner omits stage notifications.
            if self.auto_state in {AutoState.TARGET_STARTING, AutoState.TARGET_RUNNING}:
                if not self._transition_auto(AutoState.TARGET_RESTORING):
                    return
            elif self.auto_state != AutoState.TARGET_RESTORING:
                self._stop_auto(
                    f"#Multiple terminé dans un état Auto inattendu : {self.auto_state.value}."
                )
                return
            if self.target_browser_snapshot is not None and not result.browser_restored:
                self._write_clipboard(text)
                self._stop_auto("#Multiple terminé mais la fenêtre navigateur n'a pas pu être restaurée.", set_status=False)
                self._set_status("RÉSULTAT PRÊT", "Résultat #Multiple copié, mais reprise Auto impossible : navigateur non restauré.", DANGER)
                return
            workspace = getattr(self, "workspace", None)
            if workspace is not None and workspace.binding is not None and not workspace.restore_llm_workspace():
                self._stop_auto("#Multiple terminé mais le workspace LLM n'a pas pu être restauré.")
                return
            self._set_status("AUTO ACTIF", "Target App terminée : renvoi du résultat et des observations au LLM…", PURPLE)
            self._schedule_auto_action(
                "auto_delay_result_to_send_seconds",
                lambda t=text, image=sheet: self._auto_send_message(t, image),
            )
            return

        self._write_clipboard(text)
        detail = "Résultat #Multiple copié. Collez-le au LLM."
        if sheet is not None:
            detail += " Les observations visuelles seront jointes automatiquement uniquement en mode Agent Auto."
        self._set_status("RÉSULTAT PRÊT", detail, SUCCESS if result.status == ExecutionStatus.SUCCESS else WARNING)

    def _finalize_test_session_result(self, result: TestSessionResult) -> None:
        self.execution_count += 1
        self.session_info.configure(text=f"{self.execution_count} exécution{'s' if self.execution_count > 1 else ''}")
        self.history.insert("", 0, values=(result.request_id, f"TEST/{result.operation}/{result.status.value}", f"{result.duration:.2f}s"))

        reminder = ""
        every = max(1, int(self.settings.goal_reminder_every or 4))
        if self.execution_count % every == 0:
            reminder = self.goal_text.get("1.0", "end").strip()
        text = format_test_session_result(result, goal_reminder=reminder, max_output_chars=self.settings.max_output_chars)
        text = redact_secrets(text)
        if result.stdout:
            self._append_terminal("[test-session stdout]\n" + result.stdout + ("\n" if not result.stdout.endswith("\n") else ""), "stdout")
        if result.stderr:
            self._append_terminal("[test-session stderr]\n" + result.stderr + ("\n" if not result.stderr.endswith("\n") else ""), "stderr")
        sheet = compose_observation_sheet(result.observations)
        kind = {
            "OPENED": DirectiveKind.OPEN_TEST_SESSION,
            "ACTIONS": DirectiveKind.TEST_ACTIONS,
            "CLOSED": DirectiveKind.CLOSE_TEST_SESSION,
        }.get(result.operation, DirectiveKind.TEST_ACTIONS)
        self.last_result_text = text
        self.last_result_request_id = result.request_id
        self.last_result_kind = kind
        self.last_result_attachment = sheet

        interrupted = bool(getattr(self, "test_interrupted_by_user", False) or self.user_intervention.is_set())
        self.test_interrupted_by_user = False
        if interrupted:
            self._set_status(
                "AUTO PAUSÉ" if self.auto_paused else "TESTSESSION INTERROMPUE",
                "Intervention utilisateur pendant la TestSession : aucun retour automatique navigateur/presse-papiers.",
                WARNING,
            )
            return

        if self.auto_enabled and not self.auto_paused:
            if not result.llm_restored:
                detail = result.note or getattr(getattr(self, "workspace", None), "last_error", None) or "raison inconnue"
                self._stop_auto(f"TestSession terminée mais le workspace LLM n'a pas pu être restauré : {detail}")
                return
            if self.auto_state in {AutoState.TEST_OPENING, AutoState.TEST_ACTING}:
                if not self._transition_auto(AutoState.TEST_RESTORING):
                    return
            if self.auto_state == AutoState.TEST_RESTORING:
                if not self._transition_auto(AutoState.SENDING):
                    return
            elif self.auto_state == AutoState.TEST_CLOSING:
                if not self._transition_auto(AutoState.SENDING):
                    return
            elif self.auto_state != AutoState.SENDING:
                self._stop_auto(f"Résultat TestSession reçu dans un état inattendu : {self.auto_state.value}.")
                return
            self._set_status("AUTO ACTIF", f"TestSession {result.operation.lower()} : renvoi au LLM…", PURPLE)
            self._schedule_auto_action(
                "auto_delay_result_to_send_seconds",
                lambda t=text, image=sheet: self._auto_send_message(t, image),
            )
            return

        self._write_clipboard(text)
        self._set_status(
            "RÉSULTAT TESTSESSION PRÊT",
            "Résultat copié dans le presse-papiers." + (" Une observation visuelle est également disponible." if sheet is not None else ""),
            SUCCESS if result.status == ExecutionStatus.SUCCESS else WARNING,
        )

    def _refuse_pending(self) -> None:
        if not self.pending_request:
            return
        req = self.pending_request
        if self.pending_kind in {DirectiveKind.SHOW, DirectiveKind.MULTIPLE}:
            refused_kind = self.pending_kind
            self.pending_request = None
            self.pending_cwd = None
            self.pending_kind = DirectiveKind.EXECUTION
            self.pending_actions = ()
            self.run_btn.configure(state="disabled")
            self.refuse_btn.configure(state="disabled")
            label = "SHOW" if refused_kind == DirectiveKind.SHOW else "MULTIPLE"
            self._set_status(f"{label} REFUSÉ", "Action visible/interactif refusée par l'utilisateur.", WARNING)
            return

        cwd = self.pending_cwd or Path(self.project_var.get() or ".")
        result = ExecutionResult(
            request_id=req.request_id,
            status=ExecutionStatus.BLOCKED,
            exit_code=None,
            duration=0,
            cwd=cwd,
            stdout="",
            stderr="",
            command=req.command,
            note="Commande refusée par l'utilisateur ou la politique locale. Propose une alternative plus sûre.",
        )
        self.pending_request = None
        self.pending_cwd = None
        self.run_btn.configure(state="disabled")
        self.refuse_btn.configure(state="disabled")
        self._finalize_result(result)

    def _stop_execution(self) -> None:
        if self.test_session.busy:
            self.test_interrupted_by_user = True
            self.test_session.cancel_interaction()
            self._set_status("ARRÊT DEMANDÉ", "Actions TestSession interrompues. La cible reste ouverte pour inspection.", WARNING)
            return
        if self.target_runner.running:
            self.target_runner.cancel()
            self._set_status("ARRÊT DEMANDÉ", "Arrêt de la séquence Target App demandé. La cible reste ouverte pour inspection.", WARNING)
            return
        if self.executor.running:
            self.executor.cancel()
            self._set_status("ARRÊT DEMANDÉ", "Arrêt du processus en cours…", WARNING)

    def _recopy_result(self) -> None:
        if not self.last_result_text:
            messagebox.showinfo("Aucun résultat", "Aucun résultat n'a encore été produit dans cette session.")
            return
        self._write_clipboard(self.last_result_text)
        self._set_status("RÉSULTAT PRÊT", "Dernier résultat recopié dans le presse-papiers.", SUCCESS)

    # ----------------------------- Agent Auto -----------------------------

    @property
    def auto_state(self) -> AutoState:
        return self.auto_machine.state

    def _transition_auto(self, target: AutoState) -> bool:
        try:
            self.auto_machine.transition(target)
            return True
        except AutoTransitionError as exc:
            # A state-machine violation is an internal consistency failure.
            # Fail closed: stop all future Auto actions instead of attempting
            # to guess which click/command should happen next.
            self._stop_auto(f"Incohérence interne Agent Auto : {exc}")
            self._append_terminal(f"[auto-state] {exc}\n", "stderr")
            return False

    def _toggle_auto(self) -> None:
        if self.auto_enabled:
            self._stop_auto("Agent Auto arrêté par l'utilisateur.")
            return
        if not self.desktop.available:
            messagebox.showerror("Agent Auto indisponible", "Le mode Agent Auto souris/clavier est disponible uniquement sous Windows.")
            return
        try:
            self._project_root()
        except ProtocolError as exc:
            messagebox.showerror("Projet requis", str(exc))
            return
        if not self.goal_text.get("1.0", "end").strip():
            messagebox.showerror("Destination Goal requis", "Renseignez le Topic / Destination Goal avant de démarrer Agent Auto.")
            return
        if self.executor.running or self.target_runner.running or self.test_session.busy:
            messagebox.showwarning("Action en cours", "Attendez ou arrêtez l'action locale en cours avant de démarrer Agent Auto.")
            return
        self._save_settings()
        if not self.settings.auto_configured:
            self._open_auto_setup(True)
            return
        self._start_auto()

    def _open_auto_setup(self, start_after: bool) -> None:
        if self.auto_enabled:
            messagebox.showinfo("Agent Auto actif", "Arrêtez d'abord Agent Auto avant de modifier les positions de clic.")
            return
        if not self.desktop.available:
            messagebox.showerror("Agent Auto indisponible", "La configuration des clics Win32 est disponible uniquement sous Windows.")
            return

        def saved() -> None:
            self.settings = self.store.load()
            self._update_auto_controls()
            if start_after:
                self.after(150, self._start_auto)

        AutoSetupDialog(self, self.desktop, self.settings, saved)

    def _start_auto(self) -> None:
        if self.auto_enabled:
            return
        if not self.settings.auto_configured:
            self._open_auto_setup(True)
            return
        # Persist before installing the global hook: a settings write failure
        # must not leave the mouse monitor running in a half-started Auto mode.
        self._save_settings()
        # Arm the intervention flag before installing the hook. Clearing it
        # after the hook starts creates a race where a real user movement can
        # be observed and then accidentally forgotten.
        self.user_intervention.clear()
        try:
            self.auto_copy_template = load_template(self.settings.auto_copy_template_path)
            current_geometry = self.desktop.screen_geometry()
            if (
                self.settings.auto_screen_width is not None
                and self.settings.auto_screen_height is not None
                and (
                    current_geometry.x != (self.settings.auto_screen_x or 0)
                    or current_geometry.y != (self.settings.auto_screen_y or 0)
                    or current_geometry.width != self.settings.auto_screen_width
                    or current_geometry.height != self.settings.auto_screen_height
                )
            ):
                messagebox.showwarning(
                    "Écran différent",
                    "La géométrie d'écran a changé depuis le setup Agent Auto. Reconfigurez les positions avant de démarrer.",
                )
                self._open_auto_setup(True)
                return

            # Bind a deterministic LLM HWND from the current Windows Z-order.
            # The user has just clicked the Relay, so the target browser should
            # be the first eligible external window immediately below it.
            self.update_idletasks()
            binding = self.workspace.bind(
                relay_hwnd=int(self.winfo_id()),
                relay_pid=os.getpid(),
                prompt_point=self._auto_point("prompt"),
                send_point=self._auto_point("send"),
            )
            if not self.workspace.ensure_llm_workspace():
                raise DesktopAutomationUnavailable("Impossible d'activer/vérifier le workspace LLM.")
            self._append_terminal(
                f"[workspace] LLM lié : hwnd={binding.llm.hwnd} pid={binding.llm.pid} title={binding.llm.title!r}\n",
                "info",
            )

            # Agent Auto starts by attaching to the assistant reply that is already
            # visible. Do not paste or send anything here: the user may enable
            # Auto while the assistant is still writing its current command.
            baseline = self.desktop.capture_signature(self._auto_response_rect())
            self.desktop.start_physical_mouse_monitor(self._physical_mouse_event)
        except (ProtocolError, DesktopAutomationUnavailable, TemplateMatchError) as exc:
            self.auto_copy_template = None
            self.workspace.release(restore=False)
            messagebox.showerror("Agent Auto impossible", str(exc))
            return

        self.auto_enabled = True
        self.auto_paused = False
        if not self._transition_auto(AutoState.STARTING):
            return
        self._update_auto_controls()
        if self.user_intervention.is_set():
            self._pause_auto("Mouvement souris utilisateur détecté pendant l'initialisation Agent Auto.")
            return
        self._auto_attach_existing_reply(baseline)

    def _auto_attach_existing_reply(self, baseline: bytes | None = None) -> None:
        """Synchronize with the assistant answer already visible at Auto start.

        Unlike normal post-send cycles, the current reply may have finished
        before Auto was enabled. Therefore initial attachment accepts a region
        that simply remains static for the configured stability duration. If it
        is still moving, the same tracker naturally waits for it to settle.
        """
        if not self.auto_enabled or self.auto_paused:
            return
        if self.user_intervention.is_set():
            self._pause_auto("Mouvement souris utilisateur détecté avant la synchronisation initiale.")
            return
        if baseline is None:
            baseline = self.desktop.capture_signature(self._auto_response_rect())
        tracker = VisualStabilityTracker(
            stable_seconds=float(self.settings.auto_visual_stable_seconds or 3.0),
            timeout_seconds=float(self.settings.auto_visual_timeout_seconds or 180.0),
            require_motion=False,
        )
        tracker.start(baseline, time.monotonic())
        self.auto_visual_tracker = tracker
        if not self._transition_auto(AutoState.SYNCING_EXISTING_REPLY):
            return
        stable = max(1.0, float(self.settings.auto_visual_stable_seconds or 3.0))
        self._set_status(
            "AUTO ACTIF",
            f"Synchronisation sur la réponse déjà affichée : attente de {stable:g} s de stabilité avant Copier…",
            PURPLE,
        )
        self._schedule_auto(max(100, int(self.settings.auto_visual_poll_ms or 300)), self._auto_visual_tick)

    def _stop_auto(self, reason: str, *, set_status: bool = True) -> None:
        was_enabled = self.auto_enabled or self.auto_paused
        self._cancel_auto_jobs()
        if self.target_runner.running:
            self.target_runner.cancel()
        self.desktop.stop_physical_mouse_monitor()
        self.auto_enabled = False
        self.auto_paused = False
        self.auto_machine.force_off()
        self.auto_visual_tracker = None
        self.auto_copy_template = None
        self.auto_copy_baseline_sequence = None
        self.auto_pending_attachment = None
        self.target_browser_snapshot = None
        # Never replay saved window geometry when Auto stops. Releasing the
        # workspace only clears TOPMOST/binding and preserves the user's layout.
        self.workspace.release(restore=False)
        self.user_intervention.clear()
        self._update_auto_controls()
        if set_status and was_enabled:
            self._set_status("AUTO ARRÊTÉ", reason, WARNING)

    def _pause_auto(self, reason: str) -> None:
        if not self.auto_enabled or self.auto_paused:
            return
        self._cancel_auto_jobs()
        if self.target_runner.running:
            # Sticky until target_done: _stop_auto() clears the generic mouse
            # event flag, but must not re-enable clipboard/browser automation
            # for a Target App session the user has taken over.
            self.target_interrupted_by_user = True
            self.target_runner.cancel()
        if self.test_session.busy:
            self.test_interrupted_by_user = True
            self.test_session.cancel_interaction()
        self.desktop.stop_physical_mouse_monitor()
        self.auto_paused = True
        if not self._transition_auto(AutoState.PAUSED):
            return
        self.auto_visual_tracker = None
        self._update_auto_controls()
        self._set_status(
            "AUTO PAUSÉ",
            reason + " Aucun nouveau clic automatique ne sera effectué. Cliquez « Arrêter Agent Auto » pour sortir du mode.",
            WARNING,
        )

    def _schedule_auto(self, delay_ms: int, callback: Callable[[], None]) -> str | None:
        if not self.auto_enabled or self.auto_paused or self.user_intervention.is_set():
            return None
        holder: dict[str, str] = {}

        def wrapped() -> None:
            job = holder.get("job")
            if job:
                self.auto_jobs.discard(job)
            if not self.auto_enabled or self.auto_paused or self.user_intervention.is_set():
                return
            try:
                callback()
            except DesktopAutomationUnavailable as exc:
                self._stop_auto(f"Erreur d'entrée Win32 : {exc}")
            except Exception as exc:
                self._stop_auto(f"Erreur Agent Auto : {exc}")

        job = self.after(max(0, int(delay_ms)), wrapped)
        holder["job"] = job
        self.auto_jobs.add(job)
        return job

    def _auto_action_delay_ms(self, setting_name: str) -> int:
        base_seconds = float(getattr(self.settings, setting_name, 0.0) or 0.0)
        jitter_percent = float(getattr(self.settings, "auto_timing_jitter_percent", 0.0) or 0.0)
        return jittered_action_delay_ms(base_seconds, jitter_percent, self._rng)

    def _schedule_auto_action(self, setting_name: str, callback: Callable[[], None]) -> str | None:
        """Schedule a browser UI action using the persistent pacing settings."""
        return self._schedule_auto(self._auto_action_delay_ms(setting_name), callback)

    def _cancel_auto_jobs(self) -> None:
        for job in list(self.auto_jobs):
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self.auto_jobs.clear()

    def _auto_point(self, name: str) -> ScreenPoint:
        x = getattr(self.settings, f"auto_{name}_x")
        y = getattr(self.settings, f"auto_{name}_y")
        if x is None or y is None:
            raise DesktopAutomationUnavailable(f"Position Auto '{name}' non configurée.")
        return ScreenPoint(int(x), int(y))

    def _auto_response_rect(self) -> ScreenRect:
        values = (
            self.settings.auto_response_left,
            self.settings.auto_response_top,
            self.settings.auto_response_right,
            self.settings.auto_response_bottom,
        )
        if any(value is None for value in values):
            raise DesktopAutomationUnavailable("Zone visuelle 'Réponse agent' non configurée.")
        rect = ScreenRect(int(values[0]), int(values[1]), int(values[2]), int(values[3]))
        if rect.width < 80 or rect.height < 40:
            raise DesktopAutomationUnavailable("Zone visuelle 'Réponse agent' invalide ou trop petite.")
        return rect

    def _auto_send_message(self, text: str, attachment_bgr=None) -> None:
        if not self.auto_enabled or self.auto_paused:
            return
        if not self._transition_auto(AutoState.SENDING):
            return
        self.auto_visual_tracker = None
        self.auto_pending_attachment = attachment_bgr
        self._write_clipboard(text)
        delay_ms = self._auto_action_delay_ms("auto_delay_clipboard_to_prompt_seconds")
        self._set_status(
            "AUTO ACTIF",
            f"Message prêt. Clic dans le prompt dans {delay_ms / 1000:.2f} s…",
            PURPLE,
        )
        self._schedule_auto(delay_ms, self._auto_click_prompt_for_send)

    def _auto_click_prompt_for_send(self) -> None:
        workspace = getattr(self, "workspace", None)
        if workspace is not None and workspace.binding is not None and not workspace.ensure_llm_workspace():
            self._stop_auto("Workspace LLM non restauré avant le clic dans le prompt.")
            return
        self.desktop.click(self._auto_point("prompt"))
        delay_ms = self._auto_action_delay_ms("auto_delay_prompt_to_paste_seconds")
        self._set_status(
            "AUTO ACTIF",
            f"Prompt ciblé. Collage dans {delay_ms / 1000:.2f} s…",
            PURPLE,
        )
        self._schedule_auto(delay_ms, self._auto_paste_for_send)

    def _auto_paste_for_send(self) -> None:
        self.desktop.paste()
        if self.auto_pending_attachment is not None:
            delay_ms = max(0, int(float(self.settings.auto_target_attachment_delay_seconds or 0.0) * 1000))
            self._set_status(
                "AUTO ACTIF",
                f"Résultat texte collé. Ajout de l'observation visuelle dans {delay_ms / 1000:.2f} s…",
                PURPLE,
            )
            self._schedule_auto(delay_ms, self._auto_paste_attachment_for_send)
            return
        delay_ms = self._auto_action_delay_ms("auto_delay_paste_to_send_seconds")
        self._set_status(
            "AUTO ACTIF",
            f"Message collé. Envoi dans {delay_ms / 1000:.2f} s…",
            PURPLE,
        )
        self._schedule_auto(delay_ms, self._auto_send_after_paste)

    def _auto_paste_attachment_for_send(self) -> None:
        image = self.auto_pending_attachment
        if image is None:
            self._auto_send_after_paste()
            return
        self.desktop.set_clipboard_image_bgr(image)
        self.desktop.paste()
        self.auto_pending_attachment = None
        delay_ms = max(0, int(float(self.settings.auto_target_attachment_to_send_seconds or 0.0) * 1000))
        self._set_status(
            "AUTO ACTIF",
            f"Observation visuelle collée. Envoi dans {delay_ms / 1000:.2f} s…",
            PURPLE,
        )
        self._schedule_auto(delay_ms, self._auto_send_after_paste)

    def _auto_send_after_paste(self) -> None:
        workspace = getattr(self, "workspace", None)
        if workspace is not None and workspace.binding is not None and not workspace.ensure_llm_workspace():
            self._stop_auto("Workspace LLM non restauré avant le clic Envoyer.")
            return
        # Capture immediately before Send, after the configured paste-settle
        # delay. This makes the baseline causal and avoids concurrent timers.
        self._auto_prepare_visual_baseline()
        self.desktop.click(self._auto_point("send"))
        delay_ms = self._auto_action_delay_ms("auto_delay_send_to_watch_seconds")
        self._set_status(
            "AUTO ACTIF",
            f"Message envoyé. Surveillance visuelle dans {delay_ms / 1000:.2f} s…",
            PURPLE,
        )
        self._schedule_auto(delay_ms, self._auto_begin_visual_wait)

    def _auto_prepare_visual_baseline(self) -> None:
        rect = self._auto_response_rect()
        baseline = self.desktop.capture_signature(rect)
        tracker = VisualStabilityTracker(
            stable_seconds=float(self.settings.auto_visual_stable_seconds or 3.0),
            timeout_seconds=float(self.settings.auto_visual_timeout_seconds or 180.0),
            require_motion=True,
        )
        tracker.start(baseline, time.monotonic())
        self.auto_visual_tracker = tracker

    def _auto_begin_visual_wait(self) -> None:
        if self.auto_visual_tracker is None:
            self._stop_auto("Impossible d'initialiser la surveillance visuelle de la réponse agent.")
            return
        if not self._transition_auto(AutoState.WAITING_VISUAL):
            return
        stable = max(1.0, float(self.settings.auto_visual_stable_seconds or 3.0))
        self._set_status(
            "AUTO ACTIF",
            f"Réponse envoyée. Surveillance de la zone agent : Copier après {stable:g} s de stabilité…",
            PURPLE,
        )
        self._schedule_auto(max(100, int(self.settings.auto_visual_poll_ms or 300)), self._auto_visual_tick)

    def _auto_visual_tick(self) -> None:
        initial_sync = self.auto_state == AutoState.SYNCING_EXISTING_REPLY
        if self.auto_state not in {AutoState.WAITING_VISUAL, AutoState.SYNCING_EXISTING_REPLY} or self.auto_visual_tracker is None:
            return
        frame = self.desktop.capture_signature(self._auto_response_rect())
        observation = self.auto_visual_tracker.observe(frame, time.monotonic())

        if observation.state == VisualState.TIMEOUT:
            if initial_sync:
                reason = (
                    "Timeout de synchronisation initiale : la zone Réponse agent n'est pas devenue exploitable/stable. "
                    "Vérifiez la zone configurée ou augmentez le timeout."
                )
            else:
                reason = (
                    "Timeout visuel : la zone Réponse agent n'a pas produit un cycle mouvement puis stabilité. "
                    "Vérifiez la zone configurée ou augmentez le timeout."
                )
            self._stop_auto(reason)
            return
        if observation.state == VisualState.STABLE:
            prefix = "Réponse existante" if initial_sync else "Réponse"
            self._set_status(
                "AUTO ACTIF",
                f"{prefix} visuellement stable depuis {observation.stable_for:.1f} s. Copie de la commande…",
                PURPLE,
            )
            self.auto_visual_tracker = None
            delay_ms = self._auto_action_delay_ms("auto_delay_stable_to_copy_seconds")
            self._set_status(
                "AUTO ACTIF",
                f"{prefix} stable. Recherche/clic Copier dans {delay_ms / 1000:.2f} s…",
                PURPLE,
            )
            self._schedule_auto(delay_ms, self._auto_click_copy)
            return

        target = max(1.0, float(self.settings.auto_visual_stable_seconds or 3.0))
        if observation.seen_motion:
            if observation.state == VisualState.MOVING:
                detail = f"Réponse agent en mouvement ({observation.motion_ratio * 100:.2f}% de la zone modifiée)…"
            else:
                detail = f"Zone calme {observation.stable_for:.1f}/{target:g} s — attente de stabilité complète…"
        elif initial_sync:
            detail = f"Dernière réponse apparemment statique {observation.stable_for:.1f}/{target:g} s — vérification avant Copier…"
        else:
            detail = "Attente du début visuel de la nouvelle réponse agent…"
        self._set_status("AUTO ACTIF", detail, PURPLE)
        self._schedule_auto(max(100, int(self.settings.auto_visual_poll_ms or 300)), self._auto_visual_tick)

    def _auto_find_copy_button(self):
        workspace = getattr(self, "workspace", None)
        if workspace is not None and workspace.binding is not None and not workspace.ensure_llm_workspace():
            raise DesktopAutomationUnavailable("Workspace LLM non restauré avant la recherche Copier.")
        # Motion/stability still uses the configured response ROI, but Copy is
        # located independently on the complete visible virtual desktop.
        if self.auto_copy_template is None:
            self.auto_copy_template = load_template(self.settings.auto_copy_template_path)
        geometry, frame = self.desktop.capture_virtual_screen()
        scene = bgra_bytes_to_bgr(frame.pixels, frame.width, frame.height)
        match = find_template_exact(
            scene,
            self.auto_copy_template,
            threshold=float(self.settings.auto_copy_match_threshold or 0.86),
        )
        screen_x, screen_y = match.screen_center(geometry.x, geometry.y)
        point = ScreenPoint(screen_x, screen_y)
        return point, match

    def _auto_click_copy(self) -> None:
        if not self.auto_enabled or self.auto_paused or self.user_intervention.is_set():
            return
        try:
            point, match = self._auto_find_copy_button()
        except TemplateMatchError as exc:
            self._stop_auto(f"Détection visuelle Copier impossible : {exc}")
            return
        if not self.auto_enabled or self.auto_paused or self.user_intervention.is_set():
            return

        current = self._read_clipboard() or ""
        self.auto_copy_baseline_hash = self._hash(current) if current else ""
        self.auto_copy_baseline_sequence = self.desktop.clipboard_sequence_number()
        waiting_state = (
            AutoState.WAITING_INITIAL_CLIPBOARD
            if self.auto_state == AutoState.SYNCING_EXISTING_REPLY
            else AutoState.WAITING_CLIPBOARD
        )
        if not self._transition_auto(waiting_state):
            return
        self._set_status(
            "AUTO ACTIF",
            (
                f"Bouton Copier détecté à ({point.x}, {point.y}) — confiance {match.confidence:.1%}, "
                "échelle native 1.00, recherche plein écran. Clic puis attente du presse-papiers…"
            ),
            PURPLE,
        )
        self.desktop.click(point)
        timeout = max(0.5, float(self.settings.auto_clipboard_timeout_seconds or 1.5))
        self._schedule_auto(int(timeout * 1000), self._auto_clipboard_timeout)

    def _auto_clipboard_timeout(self) -> None:
        if self.auto_state not in {
            AutoState.WAITING_INITIAL_CLIPBOARD,
            AutoState.WAITING_CLIPBOARD,
        }:
            return
        self._stop_auto(
            "Le bouton Copier détecté n'a produit aucun nouveau contenu dans le presse-papiers. Vérifiez l'image de référence, la zone de recherche ou augmentez le délai de réponse."
        )

    def _auto_accept_copied_text(self, text: str) -> None:
        if not text.strip():
            self._stop_auto("Le chatbot a produit un presse-papiers vide.")
            return
        if self.auto_state == AutoState.WAITING_INITIAL_CLIPBOARD:
            target = AutoState.PROCESSING_INITIAL_REPLY
        elif self.auto_state == AutoState.WAITING_CLIPBOARD:
            target = AutoState.PROCESSING_REPLY
        else:
            self._stop_auto(
                f"Réponse presse-papiers reçue dans un état inattendu : {self.auto_state.value}."
            )
            return
        if not self._transition_auto(target):
            return
        self._set_status("AUTO ACTIF", "Réponse copiée : validation du protocole…", PURPLE)
        self._handle_agent_text(text, source_auto=True)

    def _update_auto_controls(self) -> None:
        if self.auto_enabled and self.auto_paused:
            self.auto_button.configure(text="Arrêter Agent Auto", style="Danger.TButton")
            self.auto_badge.configure(text="AUTO PAUSÉ", bg="#5c4a1f", fg=WARNING)
        elif self.auto_enabled:
            self.auto_button.configure(text="Arrêter Agent Auto", style="Danger.TButton")
            self.auto_badge.configure(text="AUTO ACTIF", bg="#3d315f", fg="#e3d9ff")
        else:
            label = "Démarrer Agent Auto" if self.settings.auto_configured else "Configurer + démarrer Auto"
            self.auto_button.configure(text=label, style="Auto.TButton")
            badge = "AUTO PRÊT" if self.settings.auto_configured else "AUTO À CONFIGURER"
            self.auto_badge.configure(text=badge, bg="#252c37", fg=MUTED)

    # ---------------------------------------------------------------------

    def _append_terminal(self, text: str, tag: str | None = None) -> None:
        self.terminal.insert("end", text, tag or ())
        self.terminal.see("end")

    def _set_status(self, title: str, detail: str, color: str) -> None:
        self.status_title.configure(text=title, fg=color)
        self.status_detail.configure(text=detail)

    def _on_close(self) -> None:
        self._save_settings()
        if self.auto_enabled:
            self._stop_auto("Fermeture de l'application.", set_status=False)
        if self.target_runner.running:
            if not messagebox.askyesno("Target App en cours", "Une séquence Target App est en cours. L'interrompre et fermer le relais ?"):
                return
            self.target_runner.cancel()
        if self.test_session.active:
            if not messagebox.askyesno("TestSession ouverte", "Une TestSession persistante est encore ouverte. La fermer avec le relais ?"):
                return
            self.test_session.force_close()
        if self.executor.running:
            if not messagebox.askyesno("Commande en cours", "Une commande est en cours. L'arrêter et fermer ?"):
                return
            self.executor.cancel()
        self.destroy()


def main() -> None:
    # Must happen before Tk creates any HWND. Otherwise Windows can virtualize
    # GUI coordinates while GDI/SendInput operate in physical pixels.
    enable_per_monitor_dpi_awareness()
    app = ClipboardAgentApp()
    app.mainloop()


if __name__ == "__main__":
    main()
