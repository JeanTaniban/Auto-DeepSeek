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
        self.layout_thread_ids = []
        self.vk_scan_ex_calls = []
        self.vk_scan_calls = []
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

    def GetKeyboardLayout(self, thread_id):
        self.layout_thread_ids.append(int(thread_id))
        return 0x040C

    def VkKeyScanExW(self, character, layout):
        self.vk_scan_ex_calls.append((character, int(layout)))
        mapping = {
            "1": (1 << 8) | 0x31,  # AZERTY semantic "1" => SHIFT + VK_1
            "é": 0x32,             # French layout semantic accented key
        }
        return mapping.get(character, -1)

    def VkKeyScanW(self, character):
        self.vk_scan_calls.append(character)
        mapping = {
            "1": (1 << 8) | 0x31,
            "é": 0x32,
        }
        return mapping.get(character, -1)

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


def test_press_key_digit_uses_active_layout_modifiers(monkeypatch):
    desktop = Win32DesktopInput()
    fake = _FakeUser32()
    sent = []
    monkeypatch.setattr(win32_input, "user32", fake)
    monkeypatch.setattr(desktop, "_require_windows", lambda: None)
    monkeypatch.setattr(desktop, "window_exists", lambda hwnd: hwnd == 42)
    monkeypatch.setattr(desktop, "_send_inputs", lambda inputs: sent.extend(inputs))

    desktop.press_key_chord("1", target_hwnd=42)

    assert [int(item.ki.wVk) for item in sent] == [
        win32_input.VK_SHIFT,
        0x31,
        0x31,
        win32_input.VK_SHIFT,
    ]
    assert [int(item.ki.dwFlags) for item in sent] == [
        0,
        0,
        win32_input.KEYEVENTF_KEYUP,
        win32_input.KEYEVENTF_KEYUP,
    ]
    assert fake.layout_thread_ids == [222]
    assert fake.vk_scan_ex_calls == [("1", 0x040C)]
    assert fake.vk_scan_calls == []


def test_press_key_accent_uses_active_layout_key(monkeypatch):
    desktop = Win32DesktopInput()
    fake = _FakeUser32()
    sent = []
    monkeypatch.setattr(win32_input, "user32", fake)
    monkeypatch.setattr(desktop, "_require_windows", lambda: None)
    monkeypatch.setattr(desktop, "window_exists", lambda hwnd: hwnd == 42)
    monkeypatch.setattr(desktop, "_send_inputs", lambda inputs: sent.extend(inputs))

    desktop.press_key_chord("é", target_hwnd=42)

    assert [int(item.ki.wVk) for item in sent] == [0x32, 0x32]
    assert [int(item.ki.dwFlags) for item in sent] == [0, win32_input.KEYEVENTF_KEYUP]
    assert fake.layout_thread_ids == [222]
    assert fake.vk_scan_ex_calls == [("é", 0x040C)]
    assert fake.vk_scan_calls == []


def test_press_key_without_target_uses_current_thread_layout_fallback(monkeypatch):
    desktop = Win32DesktopInput()
    fake = _FakeUser32()
    sent = []
    monkeypatch.setattr(win32_input, "user32", fake)
    monkeypatch.setattr(desktop, "_require_windows", lambda: None)
    monkeypatch.setattr(desktop, "_send_inputs", lambda inputs: sent.extend(inputs))

    desktop.press_key_chord("1")

    assert fake.layout_thread_ids == []
    assert fake.vk_scan_ex_calls == []
    assert fake.vk_scan_calls == ["1"]
    assert len(sent) == 4


def test_type_text_emits_utf16_unicode_units(monkeypatch):
    desktop = Win32DesktopInput()
    sent = []
    monkeypatch.setattr(desktop, "_require_windows", lambda: None)
    monkeypatch.setattr(desktop, "_send_inputs", lambda inputs: sent.extend(inputs))

    desktop.type_text("é😀")

    # é = U+00E9; 😀 = surrogate pair D83D DE00.
    assert [int(item.ki.wScan) for item in sent] == [
        0x00E9, 0x00E9,
        0xD83D, 0xD83D,
        0xDE00, 0xDE00,
    ]
    assert all(int(item.ki.dwFlags) & win32_input.KEYEVENTF_UNICODE for item in sent)
    assert [bool(int(item.ki.dwFlags) & win32_input.KEYEVENTF_KEYUP) for item in sent] == [
        False, True, False, True, False, True,
    ]
