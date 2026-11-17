#!/usr/bin/env python3
"""
Build the Windows installer (QuickLauncher-Setup-<version>.exe) with Inno Setup.

Steps: build dist/QuickLauncher.exe with PyInstaller (unless --no-build), then
compile installer/setup.iss. The installer is written to dist/.

Needs Inno Setup 6:  winget install JRSoftware.InnoSetup
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import APP_VERSION  # noqa: E402


def find_iscc() -> Path:
    candidates = [os.environ.get("ISCC", "")]
    for base in (
        os.environ.get("LOCALAPPDATA", "") + r"\Programs",
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
    ):
        candidates.append(str(Path(base) / "Inno Setup 6" / "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit(
        "Inno Setup 6 not found. Install it with:  winget install JRSoftware.InnoSetup"
    )


def main() -> int:
    if "--no-build" not in sys.argv:
        from scripts.build import build_exe

        build_exe()  # exits on failure

    exe = ROOT / "dist" / "QuickLauncher.exe"
    if not exe.is_file():
        raise SystemExit(f"{exe} is missing - build the exe first.")

    iscc = find_iscc()
    cmd = [str(iscc), f"/DAppVersion={APP_VERSION}", str(ROOT / "installer" / "setup.iss")]
    print("Building installer:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    setup = ROOT / "dist" / f"QuickLauncher-Setup-{APP_VERSION}.exe"
    print(f"\nInstaller ready: {setup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
