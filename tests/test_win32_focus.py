import os

import pytest

from clipboard_agent import win32_input
from clipboard_agent.win32_input import Win32DesktopInput


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Win32 focus behavior")


def _hwnd_value(hwnd):
    value = getattr(hwnd, "value", hwnd)
    return int(value)


class _FakeUser32:
    def __init__(self, *, iconic=False):
        self.iconic = iconic
        self.show_calls = []
        self.foreground_calls = []

    def IsIconic(self, hwnd):
        return int(self.iconic)

    def ShowWindow(self, hwnd, command):
        self.show_calls.append((_hwnd_value(hwnd), int(command)))
        return 1

    def SetForegroundWindow(self, hwnd):
        self.foreground_calls.append(_hwnd_value(hwnd))
        return 1

    def GetForegroundWindow(self):
        return self.foreground_calls[-1] if self.foreground_calls else 0


def test_activate_window_is_noop_when_already_foreground(monkeypatch):
    desktop = Win32DesktopInput()
    fake = _FakeUser32()
    monkeypatch.setattr(desktop, "window_exists", lambda hwnd: True)
    monkeypatch.setattr(desktop, "is_foreground", lambda hwnd: True)
    monkeypatch.setattr(win32_input, "user32", fake)
    desktop.activate_window(42)
    assert fake.show_calls == []
    assert fake.foreground_calls == []


def test_activate_window_does_not_restore_visible_non_minimized_window(monkeypatch):
    desktop = Win32DesktopInput()
    fake = _FakeUser32(iconic=False)
    monkeypatch.setattr(desktop, "window_exists", lambda hwnd: True)
    monkeypatch.setattr(desktop, "is_foreground", lambda hwnd: False)
    monkeypatch.setattr(win32_input, "user32", fake)
    monkeypatch.setattr(win32_input.time, "sleep", lambda _seconds: None)
    desktop.activate_window(42)
    assert fake.show_calls == []
    assert fake.foreground_calls == [42]


def test_activate_window_restores_only_when_minimized(monkeypatch):
    desktop = Win32DesktopInput()
    fake = _FakeUser32(iconic=True)
    monkeypatch.setattr(desktop, "window_exists", lambda hwnd: True)
    monkeypatch.setattr(desktop, "is_foreground", lambda hwnd: False)
    monkeypatch.setattr(win32_input, "user32", fake)
    monkeypatch.setattr(win32_input.time, "sleep", lambda _seconds: None)
    desktop.activate_window(42)
    assert fake.show_calls == [(42, win32_input.SW_RESTORE)]
    assert fake.foreground_calls == [42]
