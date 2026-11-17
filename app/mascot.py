"""
Animated mascot shown in the middle of the content area on every page.

The dancing girl is drawn in a small borderless, click-through, per-pixel
transparent window that is owned by the launcher window (so it follows it, hides
when it is minimised and never steals focus or mouse clicks). Her opacity is
configurable (`mascot_opacity`), so the pages stay readable underneath.

Frames live in assets/mascot.bin: premultiplied BGRA, one zlib blob per frame.
"""

import ctypes
import logging
import struct
import sys
import tkinter as tk
import zlib
from ctypes import wintypes
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("ui")

DEFAULT_OPACITY = 0.7

_GWL_EXSTYLE = -20
_GWLP_HWNDPARENT = -8
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_WS_EX_NOACTIVATE = 0x08000000
_GA_ROOT = 2
_SW_SHOWNOACTIVATE = 4
_ULW_ALPHA = 2
_AC_SRC_ALPHA = 1


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


def _api():
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    V = ctypes.c_void_p
    user32.GetAncestor.restype = V
    user32.GetAncestor.argtypes = [V, wintypes.UINT]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [V, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [V, ctypes.c_int, ctypes.c_ssize_t]
    user32.GetDC.restype = V
    user32.GetDC.argtypes = [V]
    user32.ReleaseDC.argtypes = [V, V]
    user32.ShowWindow.argtypes = [V, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [V]
    user32.UpdateLayeredWindow.argtypes = [V, V, V, V, V, V, wintypes.DWORD, V, wintypes.DWORD]
    gdi32.CreateCompatibleDC.restype = V
    gdi32.CreateCompatibleDC.argtypes = [V]
    gdi32.CreateDIBSection.restype = V
    gdi32.CreateDIBSection.argtypes = [V, V, wintypes.UINT, V, V, wintypes.DWORD]
    gdi32.SelectObject.restype = V
    gdi32.SelectObject.argtypes = [V, V]
    gdi32.DeleteObject.argtypes = [V]
    gdi32.DeleteDC.argtypes = [V]
    return user32, gdi32


def mascot_file() -> Optional[Path]:
    """Find assets/mascot.bin in the PyInstaller bundle, next to the exe, or the source tree."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "assets" / "mascot.bin")
        candidates.append(Path(sys.executable).resolve().parent / "assets" / "mascot.bin")
    candidates.append(Path(__file__).resolve().parent.parent / "assets" / "mascot.bin")
    return next((c for c in candidates if c.is_file()), None)


_cache: dict = {}


def load_frames():
    """Return (compressed frame blobs, width, height, fps); cached after the first call."""
    if "v" not in _cache:
        path = mascot_file()
        if path is None:
            raise FileNotFoundError("assets/mascot.bin not found")
        data = path.read_bytes()
        if data[:4] != b"QLM1":
            raise ValueError("bad mascot file")
        count, w, h, fps = struct.unpack_from("<IHHI", data, 4)
        pos, blobs = 16, []
        for _ in range(count):
            (n,) = struct.unpack_from("<I", data, pos)
            blobs.append(data[pos + 4:pos + 4 + n])
            pos += 4 + n
        _cache["v"] = (blobs, w, h, fps)
    return _cache["v"]


class MascotOverlay:
    def __init__(self, root: tk.Tk, area_widget, opacity: float = DEFAULT_OPACITY):
        self.root = root
        self.area = area_widget
        self.alpha = max(0, min(255, int(round(opacity * 255))))
        self.blobs, self.w, self.h, fps = load_frames()
        self.delay = max(20, int(1000 / max(fps, 1)))
        self.i = 0
        self.dead = False
        self.user32, self.gdi32 = _api()

        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.transient(root)
        self.top.update_idletasks()
        self.hwnd = None
        self._setup_window()

        # Off-screen 32-bit top-down bitmap the frames are copied into
        self.screen_dc = self.user32.GetDC(None)
        self.mem_dc = self.gdi32.CreateCompatibleDC(self.screen_dc)
        bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), self.w, -self.h, 1, 32, 0,
                                self.w * self.h * 4, 0, 0, 0, 0)
        self.bits = ctypes.c_void_p()
        self.bitmap = self.gdi32.CreateDIBSection(
            self.screen_dc, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        if not self.bitmap or not self.bits:
            raise OSError("CreateDIBSection failed")
        self.old_bitmap = self.gdi32.SelectObject(self.mem_dc, self.bitmap)

        self.top.bind("<Destroy>", lambda e: self._free() if e.widget is self.top else None)
        root.bind("<Configure>", lambda e: self._draw() if e.widget is root else None, add="+")
        root.bind("<Map>", lambda e: self._show() if e.widget is root else None, add="+")
        self._draw()
        self.user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
        self.top.after(self.delay, self._tick)

    def _setup_window(self):
        """(Re)apply owner + layered/click-through styles. Tk may recreate the native
        window when it hides/shows the transient, so this runs whenever the handle changed."""
        hwnd = self.user32.GetAncestor(self.top.winfo_id(), _GA_ROOT)
        if not hwnd or hwnd == self.hwnd:
            return
        self.hwnd = hwnd
        # Make the launcher window the real Win32 owner: the overlay then always stays
        # right above it (also after it gets activated) and hides while it is minimised.
        root_hwnd = self.user32.GetAncestor(self.root.winfo_id(), _GA_ROOT)
        self.user32.SetWindowLongPtrW(hwnd, _GWLP_HWNDPARENT, root_hwnd)
        style = self.user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
        self.user32.SetWindowLongPtrW(
            hwnd, _GWL_EXSTYLE,
            style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE)

    def _show(self):
        if not self.dead:
            self._setup_window()
            self.user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
            self._draw()

    def _draw(self):
        if self.dead:
            return
        try:
            ctypes.memmove(self.bits, zlib.decompress(self.blobs[self.i]), self.w * self.h * 4)
            a = self.area
            x = a.winfo_rootx() + (a.winfo_width() - self.w) // 2
            y = a.winfo_rooty() + (a.winfo_height() - self.h) // 2
            pos = wintypes.POINT(x, y)
            size = wintypes.SIZE(self.w, self.h)
            src = wintypes.POINT(0, 0)
            blend = _BLENDFUNCTION(0, 0, self.alpha, _AC_SRC_ALPHA)
            self.user32.UpdateLayeredWindow(
                self.hwnd, self.screen_dc, ctypes.byref(pos), ctypes.byref(size),
                self.mem_dc, ctypes.byref(src), 0, ctypes.byref(blend), _ULW_ALPHA)
        except tk.TclError:
            self.dead = True

    def _tick(self):
        if self.dead:
            return
        try:
            if self.root.winfo_exists() and self.root.state() == "normal":
                # Tk hides transient windows with the launcher and does not always bring
                # them back (minimise/restore, tray), so show it again when needed.
                self._setup_window()
                if not self.user32.IsWindowVisible(self.hwnd):
                    self.user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
                self.i = (self.i + 1) % len(self.blobs)
                self._draw()
            self.top.after(self.delay, self._tick)
        except tk.TclError:
            self.dead = True

    def _free(self):
        if self.dead and not self.bitmap:
            return
        self.dead = True
        try:
            self.gdi32.SelectObject(self.mem_dc, self.old_bitmap)
            self.gdi32.DeleteObject(self.bitmap)
            self.gdi32.DeleteDC(self.mem_dc)
            self.user32.ReleaseDC(None, self.screen_dc)
        except Exception:
            pass
        self.bitmap = None


def start(window, config: dict, area_key: str = "-CONTENT-") -> Optional[MascotOverlay]:
    """Attach the mascot to a freshly built main window. Never raises (it is decoration)."""
    if not config.get("mascot_enabled", True) or sys.platform != "win32":
        return None
    try:
        window.refresh()
        opacity = float(config.get("mascot_opacity", DEFAULT_OPACITY))
        overlay = MascotOverlay(window.TKroot, window[area_key].Widget, opacity)
        window.TKroot._mascot = overlay  # keep a reference for the life of the window
        return overlay
    except Exception as exc:
        logger.warning("Mascot disabled: %s", exc)
        return None
