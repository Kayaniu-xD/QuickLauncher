"""
Animated mascot shown next to the account list.

The frames are small palette PNGs with a real alpha channel (the green screen
of the source clip is already removed), stored in assets/mascot/. Tk blends
them over the widget background, so they look right on both themes.
"""

import logging
import sys
import tkinter as tk
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("ui")

FRAME_DELAY_MS = 66  # ~15 fps


def mascot_dir() -> Optional[Path]:
    """Find assets/mascot in the PyInstaller bundle, next to the exe, or the source tree."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "assets" / "mascot")
        candidates.append(Path(sys.executable).resolve().parent / "assets" / "mascot")
    candidates.append(Path(__file__).resolve().parent.parent / "assets" / "mascot")
    return next((c for c in candidates if c.is_dir()), None)


def frame_files() -> List[Path]:
    folder = mascot_dir()
    return sorted(folder.glob("*.png")) if folder else []


def first_frame() -> Optional[str]:
    """Path of the first frame (used as the static image before the animation starts)."""
    files = frame_files()
    return str(files[0]) if files else None


def start_animation(window, key: str = "-MASCOT-") -> None:
    """
    Play the frames in a loop on the Image element `key` using Tk's own timer, so it
    never touches the PySimpleGUI event loop. It pauses while the page is hidden and
    stops by itself once the window (or widget) is gone.
    """
    try:
        widget = window[key].Widget
        files = frame_files()
        if len(files) < 2:
            return
        frames = [tk.PhotoImage(master=widget, file=str(p)) for p in files]
        widget._mascot_frames = frames  # keep references alive
        state = {"i": 0}

        def tick():
            try:
                if not widget.winfo_exists():
                    return
                if widget.winfo_ismapped():
                    state["i"] = (state["i"] + 1) % len(frames)
                    widget.configure(image=frames[state["i"]])
                widget.after(FRAME_DELAY_MS, tick)
            except tk.TclError:
                pass  # window was closed

        widget.after(FRAME_DELAY_MS, tick)
    except Exception as exc:  # decoration only - never break the app for it
        logger.warning("Mascot animation disabled: %s", exc)
