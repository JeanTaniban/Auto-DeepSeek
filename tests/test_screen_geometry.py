from clipboard_agent.win32_input import ScreenPoint, ScreenRect


def test_screen_rect_normalizes_two_points():
    rect = ScreenRect.from_points(ScreenPoint(900, 700), ScreenPoint(100, 120))
    assert (rect.left, rect.top, rect.right, rect.bottom) == (100, 120, 900, 700)
    assert rect.width == 800
    assert rect.height == 580

from clipboard_agent.win32_input import ScreenGeometry, screen_point_to_absolute


def test_screen_point_to_absolute_primary_monitor_edges():
    geometry = ScreenGeometry(0, 0, 1920, 1080)
    assert screen_point_to_absolute(ScreenPoint(0, 0), geometry) == (0, 0)
    assert screen_point_to_absolute(ScreenPoint(1919, 1079), geometry) == (65535, 65535)


def test_screen_point_to_absolute_virtual_desktop_negative_origin():
    geometry = ScreenGeometry(-1920, -200, 3840, 1280)
    assert screen_point_to_absolute(ScreenPoint(-1920, -200), geometry) == (0, 0)
    assert screen_point_to_absolute(ScreenPoint(1919, 1079), geometry) == (65535, 65535)
    nx, ny = screen_point_to_absolute(ScreenPoint(0, 0), geometry)
    assert 32750 <= nx <= 32785
    assert 10200 <= ny <= 10300


def test_capture_virtual_screen_requests_complete_virtual_geometry():
    from clipboard_agent.win32_input import ScreenFrame, Win32DesktopInput

    class FakeDesktop(Win32DesktopInput):
        def screen_geometry(self):
            return ScreenGeometry(-1600, -100, 3520, 1180)

        def capture_frame(self, rect):
            self.requested_rect = rect
            return ScreenFrame(rect.width, rect.height, b"pixels")

    desktop = FakeDesktop()
    geometry, frame = desktop.capture_virtual_screen()
    assert geometry == ScreenGeometry(-1600, -100, 3520, 1180)
    assert desktop.requested_rect == ScreenRect(-1600, -100, 1920, 1080)
    assert frame.width == 3520
    assert frame.height == 1180
