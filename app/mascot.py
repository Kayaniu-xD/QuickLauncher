"""
Animated mascot shown in the middle of every page.

The dancing girl is drawn in a small borderless, click-through, per-pixel
transparent window that is owned by the launcher window (so it follows it, hides
when it is minimised and never steals focus or mouse clicks). Her height follows
the launcher window: she is centred in it and grows when the window is maximised /
made fullscreen. Her opacity is configurable (`mascot_opacity`).

Frames live in assets/mascot.bin: premultiplied BGRA, one zlib blob per frame.
Scaling uses GDI+ (part of Windows), so no extra libraries are needed.
"""

import ctypes
import logging
import struct
import sys
import tkinter as tk
import zlib
from ctypes import wintypes
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ui")

DEFAULT_OPACITY = 0.7
MIN_HEIGHT = 240        # never smaller than this (px)
MAX_HEIGHT = 3000       # nor bigger (memory guard)
HEIGHT_STEP = 8         # re-create the drawing surface only every few pixels while resizing

_GWL_EXSTYLE = -20
_GWLP_HWNDPARENT = -8
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_WS_EX_NOACTIVATE = 0x08000000
_GA_ROOT = 2
_GW_HWNDPREV = 3
_SWP_FLAGS = 0x0001 | 0x0002 | 0x0010   # NOSIZE | NOMOVE | NOACTIVATE
_SW_SHOWNOACTIVATE = 4
_ULW_ALPHA = 2
_AC_SRC_ALPHA = 1
_PIXELFORMAT_32BPP_PARGB = 0x000E200B
_INTERP_HQ_BICUBIC = 7
_COMPOSITING_SOURCE_COPY = 1
_PIXELOFFSET_HQ = 2


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


class _GdiplusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", ctypes.c_int), ("SuppressExternalCodecs", ctypes.c_int)]


_gdip_state: dict = {}


def _gdip():
    """Start GDI+ once per process and return its function table."""
    if "gp" not in _gdip_state:
        gp = ctypes.windll.gdiplus
        V, P, I = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int
        gp.GdipCreateBitmapFromScan0.argtypes = [I, I, I, I, V, P]
        gp.GdipGetImageGraphicsContext.argtypes = [V, P]
        gp.GdipSetInterpolationMode.argtypes = [V, I]
        gp.GdipSetCompositingMode.argtypes = [V, I]
        gp.GdipSetPixelOffsetMode.argtypes = [V, I]
        gp.GdipGraphicsClear.argtypes = [V, ctypes.c_uint32]
        gp.GdipDrawImageRectI.argtypes = [V, V, I, I, I, I]
        gp.GdipDeleteGraphics.argtypes = [V]
        gp.GdipDisposeImage.argtypes = [V]
        token = ctypes.c_size_t()
        status = gp.GdiplusStartup(ctypes.byref(token), ctypes.byref(_GdiplusStartupInput(1, None, 0, 0)), None)
        if status != 0:
            raise OSError(f"GdiplusStartup failed ({status})")
        _gdip_state["gp"] = gp  # kept for the life of the process
    return _gdip_state["gp"]


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
    user32.GetWindow.restype = V
    user32.GetWindow.argtypes = [V, wintypes.UINT]
    user32.SetWindowPos.argtypes = [V, V, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
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
    def __init__(self, root: tk.Tk, opacity: float = DEFAULT_OPACITY):
        self.root = root
        self.alpha = max(0, min(255, int(round(opacity * 255))))
        self.blobs, self.w, self.h, fps = load_frames()   # native frame size
        self.delay = max(20, int(1000 / max(fps, 1)))
        self.i = 0
        self.dead = False
        self.user32, self.gdi32 = _api()
        try:
            self.gp = _gdip()
        except Exception as exc:  # scaling then falls back to the native size
            logger.warning("GDI+ unavailable, mascot is not scaled: %s", exc)
            self.gp = None

        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.transient(root)
        self.top.update_idletasks()
        self.hwnd = None
        self._setup_window()

        self.screen_dc = self.user32.GetDC(None)
        self.mem_dc = self.gdi32.CreateCompatibleDC(self.screen_dc)
        self.tw = self.th = 0
        self.bitmap = self.old_bitmap = None
        self.bits = ctypes.c_void_p()
        self.g = self.dest_img = None
        self._make_surface(self.w, self.h)

        self.top.bind("<Destroy>", lambda e: self._free() if e.widget is self.top else None)
        root.bind("<Configure>", lambda e: self._draw() if e.widget is root else None, add="+")
        root.bind("<Map>", lambda e: self._show() if e.widget is root else None, add="+")
        self._draw()
        self.user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
        self.top.after(self.delay, self._tick)

    # ---------------------------------------------------------------- window
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

    def _stack_above_owner(self):
        """Keep the overlay directly above the launcher window in the z-order (Windows does
        not always do this by itself, e.g. when the launcher was never raised)."""
        owner = self.user32.GetAncestor(self.root.winfo_id(), _GA_ROOT)
        above = self.user32.GetWindow(owner, _GW_HWNDPREV)
        if above != self.hwnd:
            self.user32.SetWindowPos(self.hwnd, above or 0, 0, 0, 0, 0, _SWP_FLAGS)

    def _show(self):
        if not self.dead:
            self._setup_window()
            self.user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
            self._draw()

    # --------------------------------------------------------------- surface
    def _drop_surface(self):
        try:
            if self.g:
                self.gp.GdipDeleteGraphics(self.g)
            if self.dest_img:
                self.gp.GdipDisposeImage(self.dest_img)
            if self.bitmap:
                self.gdi32.SelectObject(self.mem_dc, self.old_bitmap)
                self.gdi32.DeleteObject(self.bitmap)
        except Exception:
            pass
        self.g = self.dest_img = self.bitmap = None

    def _make_surface(self, tw: int, th: int):
        """32-bit top-down bitmap of tw x th that frames are drawn (scaled) into."""
        self._drop_surface()
        bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), tw, -th, 1, 32, 0,
                                tw * th * 4, 0, 0, 0, 0)
        self.bits = ctypes.c_void_p()
        self.bitmap = self.gdi32.CreateDIBSection(
            self.screen_dc, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        if not self.bitmap or not self.bits:
            self.bitmap = None
            raise OSError("CreateDIBSection failed")
        self.old_bitmap = self.gdi32.SelectObject(self.mem_dc, self.bitmap)
        self.tw, self.th = tw, th
        if (tw, th) != (self.w, self.h):
            # Let GDI+ draw straight into the DIB's memory (premultiplied ARGB).
            img = ctypes.c_void_p()
            if self.gp.GdipCreateBitmapFromScan0(tw, th, tw * 4, _PIXELFORMAT_32BPP_PARGB,
                                                 self.bits, ctypes.byref(img)) != 0:
                raise OSError("GDI+ destination bitmap failed")
            g = ctypes.c_void_p()
            if self.gp.GdipGetImageGraphicsContext(img, ctypes.byref(g)) != 0:
                self.gp.GdipDisposeImage(img)
                raise OSError("GDI+ graphics failed")
            self.dest_img, self.g = img, g
            self.gp.GdipSetInterpolationMode(g, _INTERP_HQ_BICUBIC)
            self.gp.GdipSetCompositingMode(g, _COMPOSITING_SOURCE_COPY)
            self.gp.GdipSetPixelOffsetMode(g, _PIXELOFFSET_HQ)

    def _wanted_size(self):
        """Height follows the launcher window (so fullscreen => big girl), same aspect ratio."""
        if self.gp is None:
            return self.w, self.h
        th = max(MIN_HEIGHT, min(MAX_HEIGHT, self.root.winfo_height()))
        th = th // HEIGHT_STEP * HEIGHT_STEP
        return max(1, round(self.w * th / self.h)), th

    # ---------------------------------------------------------------- drawing
    def _draw(self):
        if self.dead:
            return
        try:
            tw, th = self._wanted_size()
            if (tw, th) != (self.tw, self.th):
                try:
                    self._make_surface(tw, th)
                except OSError as exc:
                    logger.warning("Mascot resize failed, using native size: %s", exc)
                    self.gp = None
                    self._make_surface(self.w, self.h)
            raw = zlib.decompress(self.blobs[self.i])
            if self.g is None:
                ctypes.memmove(self.bits, raw, self.w * self.h * 4)
            else:
                src = ctypes.c_void_p()
                self.gp.GdipCreateBitmapFromScan0(
                    self.w, self.h, self.w * 4, _PIXELFORMAT_32BPP_PARGB,
                    ctypes.cast(ctypes.c_char_p(raw), ctypes.c_void_p), ctypes.byref(src))
                self.gp.GdipGraphicsClear(self.g, 0)
                self.gp.GdipDrawImageRectI(self.g, src, 0, 0, self.tw, self.th)
                self.gp.GdipDisposeImage(src)

            r = self.root
            wx, wy, ww, wh = r.winfo_rootx(), r.winfo_rooty(), r.winfo_width(), r.winfo_height()
            x = wx + (ww - self.tw) // 2          # centred in the whole launcher window
            y = wy + (wh - self.th) // 2
            # Only show the part inside the launcher window (never hang over the desktop).
            left, top = max(x, wx), max(y, wy)
            right, bottom = min(x + self.tw, wx + ww), min(y + self.th, wy + wh)
            pos = wintypes.POINT(left, top)
            size = wintypes.SIZE(max(right - left, 1), max(bottom - top, 1))
            src_pt = wintypes.POINT(left - x, top - y)
            blend = _BLENDFUNCTION(0, 0, self.alpha, _AC_SRC_ALPHA)
            ctypes.set_last_error(0)
            ok = self.user32.UpdateLayeredWindow(
                self.hwnd, self.screen_dc, ctypes.byref(pos), ctypes.byref(size),
                self.mem_dc, ctypes.byref(src_pt), 0, ctypes.byref(blend), _ULW_ALPHA)
            self.last_update = (ok, ctypes.windll.kernel32.GetLastError(),
                                (pos.x, pos.y, size.cx, size.cy, src_pt.x, src_pt.y))
        except tk.TclError:
            self.dead = True

    def _tick(self):
        if self.dead:
            return
        try:
            if self.root.winfo_exists() and self.root.state() in ("normal", "zoomed"):
                # Tk hides transient windows with the launcher and does not always bring
                # them back (minimise/restore, tray), so show it again when needed.
                self._setup_window()
                if not self.user32.IsWindowVisible(self.hwnd):
                    self.user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
                self._stack_above_owner()
                self.i = (self.i + 1) % len(self.blobs)
                self._draw()
            self.top.after(self.delay, self._tick)
        except tk.TclError:
            self.dead = True

    def _free(self):
        if self.dead and not self.mem_dc:
            return
        self.dead = True
        self._drop_surface()
        try:
            self.gdi32.DeleteDC(self.mem_dc)
            self.user32.ReleaseDC(None, self.screen_dc)
        except Exception:
            pass
        self.mem_dc = None


def start(window, config: dict, area_key: str = "") -> Optional[MascotOverlay]:
    """Attach the mascot to a freshly built main window. Never raises (it is decoration)."""
    if not config.get("mascot_enabled", True) or sys.platform != "win32":
        return None
    try:
        window.refresh()
        opacity = float(config.get("mascot_opacity", DEFAULT_OPACITY))
        overlay = MascotOverlay(window.TKroot, opacity)
        window.TKroot._mascot = overlay  # keep a reference for the life of the window
        return overlay
    except Exception as exc:
        logger.warning("Mascot disabled: %s", exc)
        return None
