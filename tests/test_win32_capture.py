from clipboard_agent.win32_input import (
    ScreenFrame,
    ScreenRect,
    Win32DesktopInput,
    bgra_is_likely_black,
    bgra_non_dark_ratio,
)


def _solid_bgra(width: int, height: int, b: int, g: int, r: int) -> bytes:
    return bytes([b, g, r, 255]) * (width * height)


def test_near_black_frame_detection_is_conservative():
    black = _solid_bgra(32, 24, 0, 0, 0)
    dark = _solid_bgra(32, 24, 5, 5, 5)
    visible = bytearray(dark)
    # A small but meaningful HUD-like area must make the frame usable.
    for pixel in range(0, 32 * 24, 40):
        offset = pixel * 4
        visible[offset:offset + 3] = bytes([30, 80, 200])

    assert bgra_is_likely_black(black)
    assert bgra_is_likely_black(dark)
    assert not bgra_is_likely_black(bytes(visible))
    assert bgra_non_dark_ratio(bytes(visible)) > 0.003


def test_unknown_non_bgra_signature_is_not_misclassified_as_black():
    assert not bgra_is_likely_black(b"stable-frame")


def test_capture_window_client_prefers_more_informative_printwindow_fallback(monkeypatch):
    desktop = Win32DesktopInput()
    black = ScreenFrame(32, 24, _solid_bgra(32, 24, 0, 0, 0))
    rendered = ScreenFrame(32, 24, _solid_bgra(32, 24, 40, 80, 120))

    monkeypatch.setattr(desktop, "_require_windows", lambda: None)
    monkeypatch.setattr(desktop, "activate_window", lambda _hwnd: None)
    monkeypatch.setattr(desktop, "client_rect_screen", lambda _hwnd: ScreenRect(0, 0, 32, 24))
    monkeypatch.setattr(desktop, "capture_frame", lambda _rect: black)
    monkeypatch.setattr(
        desktop,
        "_capture_printwindow_client",
        lambda _hwnd, _width, _height: rendered,
    )

    assert desktop.capture_window_client(42) is rendered


def test_capture_window_client_keeps_visible_capture_when_it_is_already_valid(monkeypatch):
    desktop = Win32DesktopInput()
    visible = ScreenFrame(32, 24, _solid_bgra(32, 24, 30, 50, 70))
    fallback_called = []

    monkeypatch.setattr(desktop, "_require_windows", lambda: None)
    monkeypatch.setattr(desktop, "activate_window", lambda _hwnd: None)
    monkeypatch.setattr(desktop, "client_rect_screen", lambda _hwnd: ScreenRect(0, 0, 32, 24))
    monkeypatch.setattr(desktop, "capture_frame", lambda _rect: visible)
    monkeypatch.setattr(
        desktop,
        "_capture_printwindow_client",
        lambda *_args: fallback_called.append(True),
    )

    assert desktop.capture_window_client(42) is visible
    assert fallback_called == []
