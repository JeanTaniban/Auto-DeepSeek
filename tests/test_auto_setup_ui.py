import tkinter as tk
from types import SimpleNamespace

import pytest

from clipboard_agent.app import AutoSetupDialog
from clipboard_agent.storage import Settings, SettingsStore
from clipboard_agent.win32_input import ScreenGeometry


class _DesktopStub:
    def screen_geometry(self):
        return ScreenGeometry(0, 0, 1920, 1080)


def _make_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.geometry("120x80+0+0")
    root.update_idletasks()
    return root


def test_auto_setup_is_resizable_and_scrollable():
    root = _make_root()
    try:
        dialog = AutoSetupDialog(root, _DesktopStub(), Settings(), lambda: None)
        dialog.geometry("700x520")
        dialog.update_idletasks()
        dialog.update()

        assert tuple(bool(v) for v in dialog.resizable()) == (True, True)
        assert dialog.scrollbar.winfo_ismapped() == 1

        bbox = dialog.scroll_canvas.bbox("all")
        assert bbox is not None
        content_height = bbox[3] - bbox[1]
        visible_height = dialog.scroll_canvas.winfo_height()
        assert content_height > visible_height

        dialog.scroll_canvas.yview_moveto(0.0)
        before = dialog.scroll_canvas.yview()
        event = SimpleNamespace(num=None, delta=-120)
        assert dialog._on_mousewheel(event) == "break"
        dialog.update_idletasks()
        after = dialog.scroll_canvas.yview()
        assert after[0] > before[0]

        dialog.destroy()
    finally:
        root.destroy()


def test_auto_setup_persists_action_timings_and_jitter(tmp_path):
    root = _make_root()
    try:
        root.store = SettingsStore(base_dir=tmp_path)
        root._update_auto_controls = lambda: None
        settings = Settings()
        dialog = AutoSetupDialog(root, _DesktopStub(), settings, lambda: None)
        dialog.delay_result_to_send_var.set(0.55)
        dialog.delay_clipboard_to_prompt_var.set(0.35)
        dialog.delay_prompt_to_paste_var.set(0.45)
        dialog.delay_paste_to_send_var.set(1.20)
        dialog.delay_send_to_watch_var.set(0.65)
        dialog.delay_stable_to_copy_var.set(0.75)
        dialog.timing_jitter_var.set(22.0)
        dialog._persist_draft()

        loaded = root.store.load()
        assert loaded.auto_delay_result_to_send_seconds == pytest.approx(0.55)
        assert loaded.auto_delay_clipboard_to_prompt_seconds == pytest.approx(0.35)
        assert loaded.auto_delay_prompt_to_paste_seconds == pytest.approx(0.45)
        assert loaded.auto_delay_paste_to_send_seconds == pytest.approx(1.20)
        assert loaded.auto_delay_send_to_watch_seconds == pytest.approx(0.65)
        assert loaded.auto_delay_stable_to_copy_seconds == pytest.approx(0.75)
        assert loaded.auto_timing_jitter_percent == pytest.approx(22.0)
        dialog.destroy()
    finally:
        root.destroy()
