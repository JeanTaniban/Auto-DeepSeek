import os

import pytest

from clipboard_agent import win32_input
from clipboard_agent.win32_input import Win32DesktopInput


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Win32 focus behavior")


def _hwnd_value(hwnd):
    value = getattr(hwnd, "value", hwnd)
    return int(value)


class _FakeUser32:
    def __init__(self, *, iconic=False, first_foreground_succeeds=True):
        self.iconic = iconic
        self.first_foreground_succeeds = first_foreground_succeeds
        self.show_calls = []
        self.foreground_calls = []
        self.bring_calls = []
        self.attach_calls = []
        self.current_foreground = 7

    def IsIconic(self, hwnd):
        return int(self.iconic)

    def ShowWindow(self, hwnd, command):
        self.show_calls.append((_hwnd_value(hwnd), int(command)))
        return 1

    def SetForegroundWindow(self, hwnd):
        value = _hwnd_value(hwnd)
        self.foreground_calls.append(value)
        if self.first_foreground_succeeds or len(self.foreground_calls) > 1:
            self.current_foreground = value
            return 1
        return 0

    def GetForegroundWindow(self):
        return self.current_foreground

    def GetWindowThreadProcessId(self, hwnd, pid_ptr):
        return 222

    def BringWindowToTop(self, hwnd):
        self.bring_calls.append(_hwnd_value(hwnd))
        return 1

    def AttachThreadInput(self, source_tid, target_tid, attach):
        self.attach_calls.append((int(source_tid), int(target_tid), bool(attach)))
        return 1


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
    monkeypatch.setattr(desktop, "is_foreground", lambda hwnd: fake.current_foreground == hwnd)
    monkeypatch.setattr(win32_input, "user32", fake)
    monkeypatch.setattr(win32_input.time, "sleep", lambda _seconds: None)
    desktop.activate_window(42)
    assert fake.show_calls == []
    assert fake.foreground_calls == [42]


def test_activate_window_restores_only_when_minimized(monkeypatch):
    desktop = Win32DesktopInput()
    fake = _FakeUser32(iconic=True)
    monkeypatch.setattr(desktop, "window_exists", lambda hwnd: True)
    monkeypatch.setattr(desktop, "is_foreground", lambda hwnd: fake.current_foreground == hwnd)
    monkeypatch.setattr(win32_input, "user32", fake)
    monkeypatch.setattr(win32_input.time, "sleep", lambda _seconds: None)
    desktop.activate_window(42)
    assert fake.show_calls == [(42, win32_input.SW_RESTORE)]
    assert fake.foreground_calls == [42]


class _FakeKernel32:
    def GetCurrentThreadId(self):
        return 111


def test_activate_window_uses_input_thread_fallback_without_moving_window(monkeypatch):
    desktop = Win32DesktopInput()
    fake = _FakeUser32(iconic=False, first_foreground_succeeds=False)
    monkeypatch.setattr(desktop, "window_exists", lambda hwnd: True)
    monkeypatch.setattr(desktop, "is_foreground", lambda hwnd: fake.current_foreground == hwnd)
    monkeypatch.setattr(win32_input, "user32", fake)
    monkeypatch.setattr(win32_input, "kernel32", _FakeKernel32())
    monkeypatch.setattr(win32_input.time, "sleep", lambda _seconds: None)
    desktop.activate_window(42)
    assert fake.show_calls == []
    assert fake.foreground_calls == [42, 42]
    assert fake.bring_calls == [42]
    assert fake.attach_calls == [(111, 222, True), (111, 222, False)]
    assert fake.current_foreground == 42
