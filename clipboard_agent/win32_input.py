from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable


class DesktopAutomationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScreenPoint:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class ScreenGeometry:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ScreenRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @classmethod
    def from_points(cls, a: ScreenPoint, b: ScreenPoint) -> "ScreenRect":
        return cls(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))


def screen_point_to_absolute(point: ScreenPoint, geometry: ScreenGeometry) -> tuple[int, int]:
    """Map a desktop pixel to SendInput's 0..65535 virtual-desktop space.

    Keeping this conversion pure makes multi-monitor/negative-origin behaviour
    testable independently of Win32.
    """
    if geometry.width <= 0 or geometry.height <= 0:
        raise ValueError("Invalid screen geometry")
    nx = round((int(point.x) - int(geometry.x)) * 65535 / max(1, int(geometry.width) - 1))
    ny = round((int(point.y) - int(geometry.y)) * 65535 / max(1, int(geometry.height) - 1))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


@dataclass(frozen=True, slots=True)
class ScreenFrame:
    width: int
    height: int
    pixels: bytes  # top-down BGRA


def bgra_non_dark_ratio(
    pixels: bytes,
    *,
    dark_threshold: int = 10,
    max_samples: int = 4096,
) -> float:
    """Return the sampled fraction of BGRA pixels that are visibly non-dark.

    This intentionally detects only *near black* frames. A dark game scene with
    even a small HUD remains usable; an all-zero/near-zero capture from a
    not-yet-rendered or unsupported surface is classified as suspicious.
    Invalid non-empty buffers are treated as unknown/non-black so callers never
    reject an observation merely because a test/backend uses another signature
    representation.
    """
    if not pixels:
        return 0.0
    if len(pixels) % 4:
        return 1.0
    threshold = max(0, min(255, int(dark_threshold)))
    total_pixels = len(pixels) // 4
    step = max(1, total_pixels // max(1, int(max_samples)))
    samples = 0
    non_dark = 0
    view = memoryview(pixels)
    for pixel_index in range(0, total_pixels, step):
        offset = pixel_index * 4
        samples += 1
        if (
            view[offset] > threshold
            or view[offset + 1] > threshold
            or view[offset + 2] > threshold
        ):
            non_dark += 1
    return non_dark / max(1, samples)


def bgra_is_likely_black(
    pixels: bytes,
    *,
    dark_threshold: int = 10,
    max_non_dark_ratio: float = 0.003,
) -> bool:
    """Conservatively flag a BGRA capture that is almost entirely black."""
    if not pixels or len(pixels) % 4:
        return False
    return bgra_non_dark_ratio(
        pixels,
        dark_threshold=dark_threshold,
    ) <= max(0.0, min(1.0, float(max_non_dark_ratio)))


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    rect: ScreenRect
    client_rect: ScreenRect
    minimized: bool = False


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    hwnd: int
    pid: int
    title: str
    rect: ScreenRect
    show_cmd: int
    normal_rect: ScreenRect | None = None


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    ULONG_PTR = wintypes.WPARAM

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wintypes.POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.UINT),
            ("flags", wintypes.UINT),
            ("showCmd", wintypes.UINT),
            ("ptMinPosition", wintypes.POINT),
            ("ptMaxPosition", wintypes.POINT),
            ("rcNormalPosition", wintypes.RECT),
        ]

    LowLevelMouseProc = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1

    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_VIRTUALDESK = 0x4000
    MOUSEEVENTF_ABSOLUTE = 0x8000

    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    VK_CONTROL = 0x11
    VK_SHIFT = 0x10
    VK_MENU = 0x12
    VK_V = 0x56

    WH_MOUSE_LL = 14
    WM_MOUSEMOVE = 0x0200
    WM_CLOSE = 0x0010
    WM_QUIT = 0x0012
    LLMHF_INJECTED = 0x00000001
    LLMHF_LOWER_IL_INJECTED = 0x00000002

    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    SRCCOPY = 0x00CC0020
    CAPTUREBLT = 0x40000000
    COLORONCOLOR = 3
    DIB_RGB_COLORS = 0
    BI_RGB = 0
    SW_HIDE = 0
    SW_SHOWNORMAL = 1
    SW_SHOWMINIMIZED = 2
    SW_SHOWMAXIMIZED = 3
    SW_RESTORE = 9
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    CF_DIB = 8
    GMEM_MOVEABLE = 0x0002
    GA_ROOT = 2
    PW_CLIENTONLY = 0x00000001
    PW_RENDERFULLCONTENT = 0x00000002

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.GetClipboardSequenceNumber.argtypes = ()
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.SetWindowsHookExW.argtypes = (ctypes.c_int, LowLevelMouseProc, wintypes.HINSTANCE, wintypes.DWORD)
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    user32.GetMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.ReleaseDC.restype = ctypes.c_int
    user32.GetForegroundWindow.argtypes = ()
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = (wintypes.POINT,)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
    user32.PrintWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.EnumWindows.argtypes = (EnumWindowsProc, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowPlacement.argtypes = (wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT))
    user32.GetWindowPlacement.restype = wintypes.BOOL
    user32.SetWindowPlacement.argtypes = (wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT))
    user32.SetWindowPlacement.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = (
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT,
    )
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostMessageW.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = ()
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = ()
    user32.CloseClipboard.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.SetStretchBltMode.argtypes = (wintypes.HDC, ctypes.c_int)
    gdi32.SetStretchBltMode.restype = ctypes.c_int
    gdi32.BitBlt.argtypes = (
        wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD
    )
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.StretchBlt.argtypes = (
        wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD
    )
    gdi32.StretchBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = (
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
        ctypes.POINTER(BITMAPINFO), wintypes.UINT
    )
    gdi32.GetDIBits.restype = ctypes.c_int
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetCurrentThreadId.argtypes = ()
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalFree.restype = wintypes.HGLOBAL


def enable_per_monitor_dpi_awareness() -> str:
    """Request physical-pixel coordinates before any Tk window is created.

    GDI capture, GetCursorPos and SendInput must agree on one coordinate space.
    On scaled/mixed-DPI displays a DPI-unaware process can otherwise mix logical
    and physical pixels, which looks exactly like a constant click offset.
    """
    if os.name != "nt":
        return "not-windows"

    # Windows 10 1703+: Per Monitor V2 is the preferred model.
    setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if setter is not None:
        try:
            setter.argtypes = (ctypes.c_void_p,)
            setter.restype = wintypes.BOOL
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == (HANDLE)-4
            if setter(ctypes.c_void_p(-4)):
                return "per-monitor-v2"
        except Exception:
            pass

    # Windows 8.1 fallback.
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        set_awareness = shcore.SetProcessDpiAwareness
        set_awareness.argtypes = (ctypes.c_int,)
        set_awareness.restype = ctypes.c_long
        # PROCESS_PER_MONITOR_DPI_AWARE = 2. S_OK == 0.
        if int(set_awareness(2)) == 0:
            return "per-monitor"
    except Exception:
        pass

    # Vista+ fallback. This can fail if a manifest/runtime already selected an
    # awareness mode; failure is not fatal because that existing mode may be OK.
    legacy = getattr(user32, "SetProcessDPIAware", None)
    if legacy is not None:
        try:
            legacy.restype = wintypes.BOOL
            if legacy():
                return "system"
        except Exception:
            pass
    return "unchanged"


class Win32DesktopInput:
    """Minimal Win32 desktop input layer using only the Python standard library.

    Mouse and keyboard actions are injected with SendInput. A WH_MOUSE_LL hook
    observes physical mouse movement. Injected movements carry LLMHF_INJECTED and
    therefore do not trigger the safety callback.
    """

    def __init__(self) -> None:
        self._hook_thread: threading.Thread | None = None
        self._hook_thread_id: int | None = None
        self._hook_ready = threading.Event()
        self._hook_error: str | None = None
        self._hook_callback_ref = None
        self._mouse_callback: Callable[[int, int], None] | None = None
        self._movement_reported = threading.Event()

    @property
    def available(self) -> bool:
        return os.name == "nt"

    def _require_windows(self) -> None:
        if os.name != "nt":
            raise DesktopAutomationUnavailable("Le mode Agent Auto Win32 est disponible uniquement sous Windows.")

    def screen_geometry(self) -> ScreenGeometry:
        self._require_windows()
        x = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        y = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if width <= 0 or height <= 0:
            raise DesktopAutomationUnavailable("Impossible de déterminer la géométrie de l'écran Windows.")
        return ScreenGeometry(x, y, width, height)

    def cursor_position(self) -> ScreenPoint:
        self._require_windows()
        point = wintypes.POINT()
        if not user32.GetCursorPos(point):
            raise DesktopAutomationUnavailable("Impossible de lire la position du curseur.")
        return ScreenPoint(int(point.x), int(point.y))

    def clipboard_sequence_number(self) -> int | None:
        """Return the Windows clipboard generation counter when available.

        Unlike comparing clipboard text, this detects a fresh Copy operation
        even when it writes exactly the same text that was already present.
        """
        if os.name != "nt":
            return None
        return int(user32.GetClipboardSequenceNumber())

    def _capture_bgra(self, rect: ScreenRect, width: int, height: int) -> bytes:
        self._require_windows()
        if rect.width < 10 or rect.height < 10:
            raise DesktopAutomationUnavailable("La zone Réponse agent est trop petite.")

        geometry = self.screen_geometry()
        if (
            rect.left < geometry.x
            or rect.top < geometry.y
            or rect.right > geometry.x + geometry.width
            or rect.bottom > geometry.y + geometry.height
        ):
            raise DesktopAutomationUnavailable("La zone Réponse agent sort de la géométrie d'écran actuelle.")
        if width <= 0 or height <= 0:
            raise DesktopAutomationUnavailable("Dimensions de capture écran invalides.")

        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise DesktopAutomationUnavailable("Impossible d'accéder à l'écran Windows.")
        mem_dc = None
        bitmap = None
        old_object = None
        try:
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not mem_dc:
                raise DesktopAutomationUnavailable("Impossible de créer le contexte de capture écran.")
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
            if not bitmap:
                raise DesktopAutomationUnavailable("Impossible de créer le bitmap de capture écran.")
            old_object = gdi32.SelectObject(mem_dc, bitmap)
            gdi32.SetStretchBltMode(mem_dc, COLORONCOLOR)
            if not gdi32.StretchBlt(
                mem_dc, 0, 0, width, height,
                screen_dc, rect.left, rect.top, rect.width, rect.height, SRCCOPY
            ):
                raise DesktopAutomationUnavailable("La capture de la zone écran a échoué.")

            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height  # top-down
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = BI_RGB
            buffer = ctypes.create_string_buffer(width * height * 4)
            lines = gdi32.GetDIBits(
                mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS
            )
            if lines != height:
                raise DesktopAutomationUnavailable("Impossible de lire les pixels de la zone écran.")
            return buffer.raw
        finally:
            if mem_dc and old_object:
                gdi32.SelectObject(mem_dc, old_object)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)

    def _capture_native_bgra(self, rect: ScreenRect) -> bytes:
        """Capture visible screen pixels at 1:1 resolution with GDI BitBlt.

        This path is used for template matching.  Unlike the motion-signature
        path it never stretches the image, so a reference screenshot can be
        compared against native screen pixels without interpolation.
        """
        self._require_windows()
        if rect.width < 1 or rect.height < 1:
            raise DesktopAutomationUnavailable("Zone de capture écran invalide.")

        geometry = self.screen_geometry()
        if (
            rect.left < geometry.x
            or rect.top < geometry.y
            or rect.right > geometry.x + geometry.width
            or rect.bottom > geometry.y + geometry.height
        ):
            raise DesktopAutomationUnavailable("La zone de capture sort de la géométrie d'écran actuelle.")

        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise DesktopAutomationUnavailable("Impossible d'accéder à l'écran Windows.")
        mem_dc = None
        bitmap = None
        old_object = None
        try:
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not mem_dc:
                raise DesktopAutomationUnavailable("Impossible de créer le contexte de capture écran.")
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, rect.width, rect.height)
            if not bitmap:
                raise DesktopAutomationUnavailable("Impossible de créer le bitmap de capture écran.")
            old_object = gdi32.SelectObject(mem_dc, bitmap)
            if not gdi32.BitBlt(
                mem_dc, 0, 0, rect.width, rect.height,
                screen_dc, rect.left, rect.top, SRCCOPY | CAPTUREBLT
            ):
                raise DesktopAutomationUnavailable("La capture native de l'écran a échoué.")

            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = rect.width
            info.bmiHeader.biHeight = -rect.height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = BI_RGB
            buffer = ctypes.create_string_buffer(rect.width * rect.height * 4)
            lines = gdi32.GetDIBits(
                mem_dc, bitmap, 0, rect.height, buffer, ctypes.byref(info), DIB_RGB_COLORS
            )
            if lines != rect.height:
                raise DesktopAutomationUnavailable("Impossible de lire les pixels de la capture native.")
            return buffer.raw
        finally:
            if mem_dc and old_object:
                gdi32.SelectObject(mem_dc, old_object)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)

    def capture_frame(self, rect: ScreenRect) -> ScreenFrame:
        """Capture ``rect`` at native 1:1 resolution as top-down BGRA."""
        pixels = self._capture_native_bgra(rect)
        return ScreenFrame(rect.width, rect.height, pixels)

    def capture_virtual_screen(self) -> tuple[ScreenGeometry, ScreenFrame]:
        """Capture the complete visible Windows virtual screen at native resolution."""
        geometry = self.screen_geometry()
        rect = ScreenRect(
            geometry.x,
            geometry.y,
            geometry.x + geometry.width,
            geometry.y + geometry.height,
        )
        return geometry, self.capture_frame(rect)

    def capture_signature(self, rect: ScreenRect, *, max_width: int = 240, max_height: int = 135) -> bytes:
        """Capture a cheap downscaled BGRA signature for motion tracking."""
        self._require_windows()
        if rect.width < 10 or rect.height < 10:
            raise DesktopAutomationUnavailable("La zone Réponse agent est trop petite.")
        scale = min(1.0, max_width / rect.width, max_height / rect.height)
        width = max(1, int(round(rect.width * scale)))
        height = max(1, int(round(rect.height * scale)))
        return self._capture_bgra(rect, width, height)

    @staticmethod
    def _send_inputs(inputs: list["INPUT"]) -> None:
        if not inputs:
            return
        array_type = INPUT * len(inputs)
        array = array_type(*inputs)
        sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise DesktopAutomationUnavailable(
                f"SendInput n'a injecté que {sent}/{len(inputs)} événement(s)."
            )

    @staticmethod
    def _mouse_input(dx: int, dy: int, flags: int) -> "INPUT":
        item = INPUT()
        item.type = INPUT_MOUSE
        item.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
        return item

    @staticmethod
    def _keyboard_input(vk: int, flags: int = 0) -> "INPUT":
        item = INPUT()
        item.type = INPUT_KEYBOARD
        item.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        return item

    def move_cursor(self, point: ScreenPoint, *, verify: bool = True, tolerance: int = 2) -> ScreenPoint:
        """Move to a global desktop point using the same path as Auto clicks.

        When ``verify`` is true, GetCursorPos must confirm the final location.
        A failed conversion therefore aborts before any mouse button event.
        """
        self._require_windows()
        geometry = self.screen_geometry()
        nx, ny = screen_point_to_absolute(point, geometry)
        self._send_inputs([
            self._mouse_input(nx, ny, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK),
        ])
        if not verify:
            return point

        # SendInput queues synchronously, but cursor observation can lag by a
        # few milliseconds on a busy desktop. Retry briefly before failing.
        actual = self.cursor_position()
        for _ in range(5):
            if abs(actual.x - point.x) <= tolerance and abs(actual.y - point.y) <= tolerance:
                return actual
            time.sleep(0.01)
            actual = self.cursor_position()
        raise DesktopAutomationUnavailable(
            "Le curseur n'a pas atteint la position calculée "
            f"(attendu {point.x},{point.y} ; obtenu {actual.x},{actual.y}). "
            "Vérifiez la mise à l'échelle DPI/écran et refaites le setup Auto."
        )

    def click(self, point: ScreenPoint) -> None:
        self._require_windows()
        self.move_cursor(point, verify=True)
        self._send_inputs([
            self._mouse_input(0, 0, MOUSEEVENTF_LEFTDOWN),
            self._mouse_input(0, 0, MOUSEEVENTF_LEFTUP),
        ])

    def paste(self) -> None:
        self._require_windows()
        self._send_inputs(
            [
                self._keyboard_input(VK_CONTROL),
                self._keyboard_input(VK_V),
                self._keyboard_input(VK_V, KEYEVENTF_KEYUP),
                self._keyboard_input(VK_CONTROL, KEYEVENTF_KEYUP),
            ]
        )

    # -------------------------- Target application --------------------------

    def foreground_window(self) -> int | None:
        self._require_windows()
        hwnd = int(user32.GetForegroundWindow() or 0)
        return hwnd or None

    def root_window(self, hwnd: int | None) -> int | None:
        self._require_windows()
        if not hwnd:
            return None
        root = int(user32.GetAncestor(wintypes.HWND(int(hwnd)), GA_ROOT) or int(hwnd))
        return root or None

    def set_window_topmost(self, hwnd: int, enabled: bool) -> None:
        self._require_windows()
        if not self.window_exists(hwnd):
            raise DesktopAutomationUnavailable("La fenêtre à positionner n'existe plus.")
        after = HWND_TOPMOST if enabled else HWND_NOTOPMOST
        ok = user32.SetWindowPos(
            wintypes.HWND(int(hwnd)), wintypes.HWND(after),
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        if not ok:
            raise DesktopAutomationUnavailable("Impossible de modifier le Z-order de la fenêtre.")

    def is_foreground(self, hwnd: int | None) -> bool:
        return bool(hwnd and self.foreground_window() == int(hwnd))

    def window_at_point(self, point: ScreenPoint) -> int | None:
        self._require_windows()
        hwnd = int(user32.WindowFromPoint(wintypes.POINT(int(point.x), int(point.y))) or 0)
        if not hwnd:
            return None
        root = int(user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT) or hwnd)
        return root or None

    def snapshot_window_at_point(self, point: ScreenPoint) -> WindowSnapshot | None:
        return self.snapshot_window(self.window_at_point(point))

    def window_exists(self, hwnd: int | None) -> bool:
        if os.name != "nt" or not hwnd:
            return False
        return bool(user32.IsWindow(wintypes.HWND(int(hwnd))))

    @staticmethod
    def _window_title(hwnd: int) -> str:
        length = int(user32.GetWindowTextLengthW(wintypes.HWND(hwnd)))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
        return buf.value

    @staticmethod
    def _window_pid(hwnd: int) -> int:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value)

    def window_rect(self, hwnd: int) -> ScreenRect:
        self._require_windows()
        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            raise DesktopAutomationUnavailable("Impossible de lire le rectangle de la fenêtre cible.")
        return ScreenRect(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))

    def client_rect_screen(self, hwnd: int) -> ScreenRect:
        self._require_windows()
        rect = wintypes.RECT()
        if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            raise DesktopAutomationUnavailable("Impossible de lire la zone cliente de la fenêtre cible.")
        top_left = wintypes.POINT(int(rect.left), int(rect.top))
        bottom_right = wintypes.POINT(int(rect.right), int(rect.bottom))
        if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(top_left)):
            raise DesktopAutomationUnavailable("Impossible de convertir l'origine cliente de la fenêtre cible.")
        if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(bottom_right)):
            raise DesktopAutomationUnavailable("Impossible de convertir la taille cliente de la fenêtre cible.")
        return ScreenRect(int(top_left.x), int(top_left.y), int(bottom_right.x), int(bottom_right.y))

    def window_info(self, hwnd: int) -> WindowInfo:
        self._require_windows()
        if not self.window_exists(hwnd):
            raise DesktopAutomationUnavailable("La fenêtre cible n'existe plus.")
        return WindowInfo(
            hwnd=int(hwnd),
            pid=self._window_pid(int(hwnd)),
            title=self._window_title(int(hwnd)),
            rect=self.window_rect(int(hwnd)),
            client_rect=self.client_rect_screen(int(hwnd)),
            minimized=bool(user32.IsIconic(wintypes.HWND(int(hwnd)))),
        )

    def enumerate_windows(self, pids: set[int] | None = None) -> list[WindowInfo]:
        self._require_windows()
        wanted = {int(pid) for pid in pids} if pids else None
        result: list[WindowInfo] = []

        @EnumWindowsProc
        def callback(hwnd, _lparam):
            raw = int(hwnd)
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = self._window_pid(raw)
            if wanted is not None and pid not in wanted:
                return True
            title = self._window_title(raw)
            try:
                rect = self.window_rect(raw)
                client = self.client_rect_screen(raw)
            except DesktopAutomationUnavailable:
                return True
            if rect.width < 80 or rect.height < 40 or client.width < 20 or client.height < 20:
                return True
            result.append(WindowInfo(raw, pid, title, rect, client, bool(user32.IsIconic(hwnd))))
            return True

        if not user32.EnumWindows(callback, 0):
            raise DesktopAutomationUnavailable("Énumération des fenêtres Windows impossible.")
        return result

    def best_window_for_pids(self, pids: set[int]) -> WindowInfo | None:
        self._require_windows()
        if not pids:
            return None
        foreground = self.foreground_window()
        if foreground and self._window_pid(foreground) in pids:
            try:
                info = self.window_info(foreground)
                if info.rect.width >= 80 and info.rect.height >= 40:
                    return info
            except DesktopAutomationUnavailable:
                pass
        windows = self.enumerate_windows(pids)
        if not windows:
            return None
        # Prefer a titled client window; otherwise choose the largest visible one.
        return max(
            windows,
            key=lambda item: (1 if item.title.strip() else 0, item.client_rect.width * item.client_rect.height),
        )

    def snapshot_window(self, hwnd: int | None) -> WindowSnapshot | None:
        self._require_windows()
        if not hwnd or not self.window_exists(hwnd):
            return None
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not user32.GetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(placement)):
            raise DesktopAutomationUnavailable("Impossible de mémoriser la disposition de la fenêtre navigateur.")
        normal = placement.rcNormalPosition
        return WindowSnapshot(
            hwnd=int(hwnd),
            pid=self._window_pid(int(hwnd)),
            title=self._window_title(int(hwnd)),
            rect=self.window_rect(int(hwnd)),
            show_cmd=int(placement.showCmd),
            normal_rect=ScreenRect(int(normal.left), int(normal.top), int(normal.right), int(normal.bottom)),
        )

    def snapshot_foreground_window(self) -> WindowSnapshot | None:
        return self.snapshot_window(self.foreground_window())

    def activate_window(self, hwnd: int) -> None:
        self._require_windows()
        if not self.window_exists(hwnd):
            raise DesktopAutomationUnavailable("La fenêtre cible n'existe plus.")
        if self.is_foreground(hwnd):
            return

        target = wintypes.HWND(int(hwnd))
        # SW_RESTORE is restricted to a genuinely minimized window. No
        # geometry is reapplied during an ordinary workspace handoff.
        if user32.IsIconic(target):
            user32.ShowWindow(target, SW_RESTORE)
            time.sleep(0.03)

        # First try the normal API. Windows can deny this when the foreground
        # currently belongs to the launched Target App.
        user32.SetForegroundWindow(target)
        time.sleep(0.03)
        if self.is_foreground(hwnd):
            return

        # Deterministic fallback: temporarily join this worker thread to the
        # current foreground input queue, raise only the target Z-order, and
        # retry foreground activation. This does not move or resize windows.
        current_tid = int(kernel32.GetCurrentThreadId() or 0)
        foreground = int(user32.GetForegroundWindow() or 0)
        foreground_tid = 0
        if foreground:
            pid = wintypes.DWORD()
            foreground_tid = int(
                user32.GetWindowThreadProcessId(wintypes.HWND(foreground), ctypes.byref(pid)) or 0
            )
        attached = False
        try:
            if current_tid and foreground_tid and current_tid != foreground_tid:
                attached = bool(user32.AttachThreadInput(current_tid, foreground_tid, True))
            user32.BringWindowToTop(target)
            user32.SetForegroundWindow(target)
            time.sleep(0.03)
        finally:
            if attached:
                user32.AttachThreadInput(current_tid, foreground_tid, False)

        if not self.is_foreground(hwnd):
            actual = int(user32.GetForegroundWindow() or 0)
            raise DesktopAutomationUnavailable(
                f"Impossible de placer la fenêtre cible au premier plan (cible={int(hwnd)}, foreground={actual})."
            )

    def restore_window(self, snapshot: WindowSnapshot | None) -> bool:
        self._require_windows()
        if snapshot is None or not self.window_exists(snapshot.hwnd):
            return False
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not user32.GetWindowPlacement(wintypes.HWND(snapshot.hwnd), ctypes.byref(placement)):
            return False
        normal = snapshot.normal_rect or snapshot.rect
        placement.showCmd = int(snapshot.show_cmd or SW_SHOWNORMAL)
        placement.rcNormalPosition = wintypes.RECT(normal.left, normal.top, normal.right, normal.bottom)
        if not user32.SetWindowPlacement(wintypes.HWND(snapshot.hwnd), ctypes.byref(placement)):
            return False
        if placement.showCmd == SW_SHOWMINIMIZED:
            # The browser must be usable by Agent Auto after restoration.
            user32.ShowWindow(wintypes.HWND(snapshot.hwnd), SW_RESTORE)
        else:
            user32.ShowWindow(wintypes.HWND(snapshot.hwnd), int(placement.showCmd or SW_SHOWNORMAL))

        # Restore the actual on-screen rectangle as well. WINDOWPLACEMENT uses
        # workspace coordinates and can otherwise drift by taskbar/DPI offsets.
        # For a maximized window Windows owns the final rectangle, so the saved
        # placement is the authoritative source instead.
        if int(snapshot.show_cmd) != SW_SHOWMAXIMIZED:
            expected = snapshot.rect
            user32.SetWindowPos(
                wintypes.HWND(snapshot.hwnd), None,
                expected.left, expected.top, expected.width, expected.height,
                SWP_NOZORDER | SWP_NOACTIVATE,
            )
        user32.SetForegroundWindow(wintypes.HWND(snapshot.hwnd))
        time.sleep(0.05)

        try:
            current = self.window_rect(snapshot.hwnd)
        except DesktopAutomationUnavailable:
            return False
        expected = snapshot.rect
        tolerance = 24
        return all(
            abs(a - b) <= tolerance
            for a, b in (
                (current.left, expected.left),
                (current.top, expected.top),
                (current.right, expected.right),
                (current.bottom, expected.bottom),
            )
        )

    def close_window(self, hwnd: int) -> None:
        self._require_windows()
        if self.window_exists(hwnd):
            user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)

    def click_window_client(self, hwnd: int, x: int, y: int) -> ScreenPoint:
        self._require_windows()
        # Focus first, then resolve client coordinates. If a minimized target
        # had to be restored, its client rectangle may have changed.
        self.activate_window(hwnd)
        client = self.client_rect_screen(hwnd)
        if x < 0 or y < 0 or x >= client.width or y >= client.height:
            raise DesktopAutomationUnavailable(
                f"#Click ({x},{y}) sort de la zone cliente {client.width}x{client.height} de la cible."
            )
        point = ScreenPoint(client.left + int(x), client.top + int(y))
        self.click(point)
        return point

    @staticmethod
    def _unicode_keyboard_input(code_unit: int, flags: int = 0) -> "INPUT":
        item = INPUT()
        item.type = INPUT_KEYBOARD
        item.ki = KEYBDINPUT(0, int(code_unit), KEYEVENTF_UNICODE | flags, 0, 0)
        return item

    def type_text(self, text: str) -> None:
        self._require_windows()
        inputs: list[INPUT] = []
        raw = str(text).encode("utf-16-le", errors="surrogatepass")
        for offset in range(0, len(raw), 2):
            unit = int.from_bytes(raw[offset:offset + 2], "little")
            inputs.append(self._unicode_keyboard_input(unit))
            inputs.append(self._unicode_keyboard_input(unit, KEYEVENTF_KEYUP))
        self._send_inputs(inputs)

    @staticmethod
    def _vk_for_name(name: str) -> int:
        key = name.upper()
        mapping = {
            "CTRL": VK_CONTROL, "SHIFT": VK_SHIFT, "ALT": VK_MENU,
            "ENTER": 0x0D, "ESC": 0x1B, "SPACE": 0x20, "TAB": 0x09,
            "BACKSPACE": 0x08, "DELETE": 0x2E, "INSERT": 0x2D,
            "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDN": 0x22,
            "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
        }
        if key in mapping:
            return mapping[key]
        if len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"):
            return ord(key)
        if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
            return 0x70 + int(key[1:]) - 1
        raise DesktopAutomationUnavailable(f"Touche non supportée : {name}")

    def press_key_chord(self, chord: str) -> None:
        self._require_windows()
        parts = [part.strip().upper() for part in chord.split("+") if part.strip()]
        if not parts:
            raise DesktopAutomationUnavailable("Raccourci clavier vide.")
        vks = [self._vk_for_name(part) for part in parts]
        down = [self._keyboard_input(vk) for vk in vks]
        up = [self._keyboard_input(vk, KEYEVENTF_KEYUP) for vk in reversed(vks)]
        self._send_inputs([*down, *up])

    def _capture_printwindow_client(self, hwnd: int, width: int, height: int) -> ScreenFrame | None:
        """Best-effort Win32 fallback for a client surface that screen BitBlt sees as black.

        PrintWindow asks the target to render its client into our memory DC. It
        is not guaranteed for every GPU surface, therefore failure simply
        returns None and the visible-screen capture remains authoritative.
        """
        self._require_windows()
        if width <= 0 or height <= 0 or not self.window_exists(hwnd):
            return None
        screen_dc = user32.GetDC(None)
        if not screen_dc:
            return None
        mem_dc = None
        bitmap = None
        old_object = None
        try:
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not mem_dc:
                return None
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
            if not bitmap:
                return None
            old_object = gdi32.SelectObject(mem_dc, bitmap)
            if not user32.PrintWindow(
                wintypes.HWND(int(hwnd)),
                mem_dc,
                PW_CLIENTONLY | PW_RENDERFULLCONTENT,
            ):
                return None

            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = BI_RGB
            buffer = ctypes.create_string_buffer(width * height * 4)
            lines = gdi32.GetDIBits(
                mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS
            )
            if lines != height:
                return None
            return ScreenFrame(width, height, buffer.raw)
        finally:
            if mem_dc and old_object:
                gdi32.SelectObject(mem_dc, old_object)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)

    def capture_window_client(self, hwnd: int) -> ScreenFrame:
        self._require_windows()
        self.activate_window(hwnd)
        rect = self.client_rect_screen(hwnd)
        if rect.width < 20 or rect.height < 20:
            raise DesktopAutomationUnavailable("La zone cliente de la cible est trop petite pour #Observe.")

        visible = self.capture_frame(rect)
        if not bgra_is_likely_black(visible.pixels):
            return visible

        # Some accelerated/windowed surfaces can transiently appear black in a
        # screen DC. Try the target-rendered client and keep whichever capture
        # contains more visible information.
        try:
            rendered = self._capture_printwindow_client(hwnd, rect.width, rect.height)
        except Exception:
            rendered = None
        if rendered is not None:
            if bgra_non_dark_ratio(rendered.pixels) > bgra_non_dark_ratio(visible.pixels):
                return rendered
        return visible

    def set_clipboard_image_bgr(self, image_bgr) -> None:
        """Place a BGR numpy image on the Windows clipboard as CF_DIB."""
        self._require_windows()
        try:
            import cv2
            ok, encoded = cv2.imencode(".bmp", image_bgr)
        except Exception as exc:
            raise DesktopAutomationUnavailable(f"Impossible d'encoder l'observation pour le presse-papiers : {exc}") from exc
        if not ok:
            raise DesktopAutomationUnavailable("Impossible d'encoder l'observation pour le presse-papiers.")
        bmp = encoded.tobytes()
        if len(bmp) <= 14:
            raise DesktopAutomationUnavailable("Bitmap d'observation invalide.")
        dib = bmp[14:]  # CF_DIB omits the BITMAPFILEHEADER.

        opened = False
        for _ in range(10):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.02)
        if not opened:
            raise DesktopAutomationUnavailable("Impossible d'ouvrir le presse-papiers pour l'image d'observation.")
        handle = None
        try:
            if not user32.EmptyClipboard():
                raise DesktopAutomationUnavailable("Impossible de vider le presse-papiers avant l'image.")
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
            if not handle:
                raise DesktopAutomationUnavailable("Allocation du bitmap presse-papiers impossible.")
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                raise DesktopAutomationUnavailable("Verrouillage du bitmap presse-papiers impossible.")
            try:
                ctypes.memmove(pointer, dib, len(dib))
            finally:
                kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_DIB, handle):
                raise DesktopAutomationUnavailable("Écriture de l'image dans le presse-papiers impossible.")
            # Ownership is transferred to Windows after SetClipboardData succeeds.
            handle = None
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)

    def start_physical_mouse_monitor(self, callback: Callable[[int, int], None], timeout: float = 1.5) -> None:
        self._require_windows()
        self.stop_physical_mouse_monitor()
        self._hook_ready.clear()
        self._movement_reported.clear()
        self._hook_error = None
        self._mouse_callback = callback
        self._hook_thread = threading.Thread(target=self._hook_worker, daemon=True, name="agent-auto-mouse-hook")
        self._hook_thread.start()
        if not self._hook_ready.wait(timeout):
            self.stop_physical_mouse_monitor()
            raise DesktopAutomationUnavailable("Le hook de sécurité souris Windows ne répond pas.")
        if self._hook_error:
            error = self._hook_error
            self.stop_physical_mouse_monitor()
            raise DesktopAutomationUnavailable(error)

    def _hook_worker(self) -> None:
        self._hook_thread_id = int(kernel32.GetCurrentThreadId())

        @LowLevelMouseProc
        def hook_proc(n_code, w_param, l_param):
            if n_code >= 0 and int(w_param) == WM_MOUSEMOVE:
                data = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                injected = bool(data.flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED))
                if not injected and not self._movement_reported.is_set():
                    self._movement_reported.set()
                    callback = self._mouse_callback
                    if callback is not None:
                        try:
                            callback(int(data.pt.x), int(data.pt.y))
                        except Exception:
                            pass
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._hook_callback_ref = hook_proc
        module = kernel32.GetModuleHandleW(None)
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, hook_proc, module, 0)
        if not hook:
            self._hook_error = f"Impossible d'installer le hook souris Win32 (erreur {ctypes.get_last_error()})."
            self._hook_ready.set()
            return

        self._hook_ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            user32.UnhookWindowsHookEx(hook)
            self._hook_callback_ref = None
            self._hook_thread_id = None

    def stop_physical_mouse_monitor(self) -> None:
        thread_id = self._hook_thread_id
        if os.name == "nt" and thread_id:
            try:
                user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        thread = self._hook_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        self._hook_thread = None
        self._hook_thread_id = None
        self._mouse_callback = None
        self._movement_reported.clear()
