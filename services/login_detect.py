"""
Detect the Wizard101 login screen from the window's pixels.

wizwalker's memory hooks cannot be relied on (their signatures break on game
updates and the client needs a registry install entry), so instead the game
window is captured and checked for the login dialog: a small
parchment-blue panel, centred on an otherwise black screen, with yellow-framed
input fields.
"""

import ctypes
from ctypes import wintypes
from typing import Optional, Tuple

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class Frame:
    """A captured BGRA frame with cheap per-pixel access."""

    def __init__(self, width: int, height: int, data: bytes):
        self.width = width
        self.height = height
        self.data = data  # top-down BGRA

    def rgb(self, x: int, y: int) -> Tuple[int, int, int]:
        i = (y * self.width + x) * 4
        d = self.data
        return d[i + 2], d[i + 1], d[i]


def capture_client(hwnd: int) -> Optional[Frame]:
    """
    Capture the window's client area.

    Uses a plain BitBlt from the window's own DC: it works while the window is in
    the background or covered by other windows, and - unlike PrintWindow, which
    forces the game to repaint and made it flicker every poll - it doesn't
    disturb the game at all.
    """
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 64 or height < 64:  # minimised / not yet sized
        return None

    hdc_window = user32.GetDC(hwnd)
    if not hdc_window:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    bitmap = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    old = gdi32.SelectObject(hdc_mem, bitmap)
    try:
        if not gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_window, 0, 0, SRCCOPY):
            return None
        bmi = _BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.biWidth = width
        bmi.biHeight = -height  # negative = top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = BI_RGB
        buf = (ctypes.c_ubyte * (width * height * 4))()
        got = gdi32.GetDIBits(hdc_mem, bitmap, 0, height, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
        if got == 0:
            return None
        return Frame(width, height, bytes(buf))
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)


def _is_black(rgb: Tuple[int, int, int]) -> bool:
    return rgb[0] < 16 and rgb[1] < 16 and rgb[2] < 16


def _is_parchment_blue(rgb: Tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return b > 150 and g > 130 and r < 170 and b > r + 25 and b >= g


def _is_yellow(rgb: Tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r > 200 and g > 200 and b < 90


def login_stats(frame: Frame) -> dict:
    """Raw measurements behind is_login_screen (also handy for debugging)."""
    w, h = frame.width, frame.height

    # 1) Outside the centre the login screen is plain black.
    black = total = 0
    step_x, step_y = max(w // 40, 1), max(h // 40, 1)
    for y in range(0, h, step_y):
        for x in range(0, w, step_x):
            if abs(x - w / 2) < w * 0.22 and abs(y - h / 2) < h * 0.25:
                continue
            total += 1
            black += _is_black(frame.rgb(x, y))

    # 2) The dialog: a block of parchment-blue + yellow field borders around the centre.
    blue = yellow = samples = 0
    x0, x1 = int(w * 0.5 - 200), int(w * 0.5 + 200)
    y0, y1 = int(h * 0.5 - 200), int(h * 0.5 + 200)
    for y in range(max(y0, 0), min(y1, h), 3):
        for x in range(max(x0, 0), min(x1, w), 3):
            samples += 1
            px = frame.rgb(x, y)
            blue += _is_parchment_blue(px)
            yellow += _is_yellow(px)

    return {
        "black_ratio": black / total if total else 0.0,
        "blue_ratio": blue / samples if samples else 0.0,
        "yellow_ratio": yellow / samples if samples else 0.0,
    }


def is_login_screen(frame: Frame) -> bool:
    s = login_stats(frame)
    return s["black_ratio"] > 0.9 and s["blue_ratio"] > 0.05 and s["yellow_ratio"] > 0.002


def _is_tan(rgb: Tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return 150 < r < 215 and 115 < g < 175 and 70 < b < 125 and r - b > 60


def _is_maroon(rgb: Tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return 100 < r < 170 and g < 60 and 30 < b < 100 and r > g + 60


def error_stats(frame: Frame) -> dict:
    w, h = frame.width, frame.height
    tan = maroon = samples = 0
    for y in range(max(int(h * 0.5 - 150), 0), min(int(h * 0.5 + 150), h), 3):
        for x in range(max(int(w * 0.5 - 250), 0), min(int(w * 0.5 + 250), w), 3):
            samples += 1
            px = frame.rgb(x, y)
            tan += _is_tan(px)
            maroon += _is_maroon(px)
    return {
        "tan_ratio": tan / samples if samples else 0.0,
        "maroon_ratio": maroon / samples if samples else 0.0,
    }


def is_error_dialog(frame: Frame) -> bool:
    """
    The game's "PROBLEM" popup (e.g. "Invalid username or password"): a large tan
    parchment panel in the middle of the screen with maroon QUIT / RECONNECT
    buttons. The background behind it varies (grey dim, splash screen), so only
    the panel itself is checked.
    """
    s = error_stats(frame)
    return s["tan_ratio"] > 0.25 and s["maroon_ratio"] > 0.02


def check_error_dialog(hwnd: int) -> Optional[bool]:
    """True/False if an error popup is (not) showing, None if the window can't be captured."""
    frame = capture_client(hwnd)
    if frame is None:
        return None
    return is_error_dialog(frame)


def check_login_screen(hwnd: int) -> Optional[bool]:
    """True/False if the login dialog is (not) showing, None if the window can't be captured."""
    frame = capture_client(hwnd)
    if frame is None:
        return None
    return is_login_screen(frame)
