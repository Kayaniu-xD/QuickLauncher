"""Launcher logic and Wizard101 instance management."""

import asyncio
import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional

try:
    import winreg
except Exception:  # pragma: no cover - only for non-Windows
    winreg = None

try:
    from wizwalker.utils import (
        get_all_wizard_handles,
        set_foreground_window,
        set_window_title,
    )

    WIZWALKER_AVAILABLE = True
except Exception:
    WIZWALKER_AVAILABLE = False

from core.config import REGISTRY_KEY
from security.crypto import Account
from services.login_detect import check_error_dialog, check_login_screen
from services.playtime_tracker import PlaytimeTracker


def get_wiz_install(config: dict) -> Path:
    """Get Wizard101 installation path from config or registry."""
    override_path = os.getenv("WIZ_INSTALL_OVERRIDE", "").strip()
    if override_path:
        return Path(override_path).absolute()

    # Check current region's custom install path first
    current_region = config.get("current_region", "de")
    region_install = config.get(f"region_install_{current_region}", "").strip()
    if region_install:
        candidate = Path(region_install)
        if candidate.exists():
            return candidate.absolute()

    # Fall back to old install_path field
    install_path = config.get("install_path", "").strip()
    if install_path:
        candidate = Path(install_path)
        if candidate.exists():
            return candidate.absolute()

    region = str(config.get("region", "de")).lower()
    default_install = config.get("default_install", "")
    if region == "us":
        default_install = config.get("default_install_us", default_install)
    default_install_path = Path(default_install)
    if default_install_path.exists():
        return default_install_path

    if winreg is not None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                REGISTRY_KEY,
                0,
                winreg.KEY_READ,
            ) as key:
                install_location = Path(
                    winreg.QueryValueEx(key, "InstallLocation")[0]
                ).absolute()
                return install_location
        except OSError as exc:
            raise Exception("Wizard101 install not found") from exc

    raise Exception("Wizard101 install not found")


def build_launch_args(config: dict, install_dir: Path) -> List[str]:
    """Build command-line arguments for Wizard101 client."""
    exe = install_dir / "Bin" / "WizardGraphicalClient.exe"
    if not exe.exists():
        raise FileNotFoundError(f"WizardGraphicalClient.exe missing in {exe}")

    # Get region-specific server and port
    current_region = config.get("current_region", "de")
    login_server = config.get(f"region_server_{current_region}", "login-de.eu.wizard101.com")
    login_port = config.get(f"region_port_{current_region}", 12000)

    args = [
        str(exe),
        "-L",
        str(login_server),
        str(login_port),
        "-A",
        str(current_region),
    ]
    extra_args = config.get("extra_args", [])
    if isinstance(extra_args, list):
        args.extend([str(arg) for arg in extra_args])
    return args


def start_instance(config: dict) -> None:
    """Start a single Wizard101 instance."""
    location = get_wiz_install(config)
    args = build_launch_args(config, location)
    subprocess.Popen(args, cwd=str(location / "Bin"))


def get_wizard_handles_safe() -> List[int]:
    """Safely get all active Wizard101 handles."""
    if not WIZWALKER_AVAILABLE:
        return []
    try:
        return list(get_all_wizard_handles())
    except Exception as exc:
        logging.getLogger("wizwalker").warning(
            "Failed to get wizard handles: %s", exc
        )
        return []


def apply_window_options(config: dict, handle: int, account: Account) -> None:
    """Apply window title and foreground options after login."""
    if not WIZWALKER_AVAILABLE:
        return
    if config.get("set_window_title"):
        template = config.get("window_title_template", "{name} ({username})")
        try:
            title = template.format(
                name=account.name, username=account.username
            )
            set_window_title(handle, title)
        except Exception as exc:
            logging.getLogger("launcher").warning(
                "Failed to format window title: %s", exc
            )
    if config.get("foreground_on_login"):
        set_foreground_window(handle)


WM_KEYDOWN = 0x100
WM_KEYUP = 0x101
WM_CHAR = 0x102
VK_BACKSPACE = 8
VK_TAB = 9
VK_ENTER = 13


def _key_lparam(vk: int, key_up: bool = False) -> int:
    """lParam for a key message (repeat count + scan code + transition flags)."""
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    lparam = 1 | (scan << 16)
    if key_up:
        lparam |= (1 << 30) | (1 << 31)
    return lparam


async def send_login(
    handle: int,
    username: str,
    password: str,
    step_delay: float = 0.1,
    submit: bool = True,
) -> None:
    """
    Type the credentials into the login screen.

    Sequence: username -> Tab -> Backspace -> password -> Enter.

    Text and Backspace are sent as WM_CHAR. Tab and Enter have to be real key
    presses (WM_KEYDOWN + WM_KEYUP): the game ignores them as plain characters,
    which left both strings in the username field and never submitted the form.
    Nothing needs the window to be in the foreground.
    """
    send = ctypes.windll.user32.SendMessageW

    def send_chars(chars: str) -> None:
        for char in chars:
            send(handle, WM_CHAR, ord(char), 0)

    def press(vk: int) -> None:
        send(handle, WM_KEYDOWN, vk, _key_lparam(vk))
        send(handle, WM_KEYUP, vk, _key_lparam(vk, key_up=True))

    send_chars(username)
    await asyncio.sleep(step_delay)
    press(VK_TAB)                              # move to the password field
    await asyncio.sleep(step_delay * 2)
    send(handle, WM_CHAR, VK_BACKSPACE, 0)     # requested extra Backspace
    await asyncio.sleep(step_delay)
    send_chars(password)
    await asyncio.sleep(step_delay)
    if submit:
        press(VK_ENTER)                        # like Tab: ignored as a plain character


async def _wait_for_login_screen(handle: int, timeout: float, poll: float = 0.2) -> Optional[bool]:
    """
    Wait until the login dialog is really on screen (seen on two checks in a row).

    Returns True when found, False on timeout, None if the window can never be
    captured (caller then falls back to a fixed wait).
    """
    deadline = time.monotonic() + timeout
    hits = 0
    captured_once = False
    while time.monotonic() < deadline:
        try:
            visible = check_login_screen(handle)
        except Exception:
            visible = None
        if visible is not None:
            captured_once = True
            hits = hits + 1 if visible else 0
            if hits >= 2:
                return True
        await asyncio.sleep(poll)
    return False if captured_once else None


async def _wait_for_login_result(handle: int, timeout: float = 20.0, poll: float = 0.25) -> str:
    """
    Watch the game after Enter was pressed.

    Returns:
        "error"        - the game shows its PROBLEM popup (e.g. wrong password)
        "still_login"  - the login dialog is still there (Enter had no effect)
        "ok"           - neither of those within the timeout (login went through)
        "unknown"      - the window could not be captured
    """
    deadline = time.monotonic() + timeout
    # Give the game a moment to react before judging "still on the login dialog".
    settle_until = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        try:
            error = check_error_dialog(handle)
            login = check_login_screen(handle)
        except Exception:
            return "unknown"
        if error is None or login is None:
            return "unknown"
        if error:
            return "error"
        if login and time.monotonic() > settle_until:
            return "still_login"
        await asyncio.sleep(poll)
    return "ok"


async def login_when_ready(handle: int, account: Account, config: dict) -> bool:
    """
    Wait for one client's login screen, log in, and verify the result.

    Returns True if the login was sent and nothing indicates it failed.
    """
    logger = logging.getLogger("launcher")
    timeout = float(config.get("login_screen_timeout_seconds", 90))
    fallback_wait = float(config.get("login_wait_seconds", 2))

    found = await _wait_for_login_screen(handle, timeout)
    if found is None:
        logger.warning(
            "Cannot capture the game window to detect the login screen; "
            "falling back to a fixed %.0fs wait for %s", fallback_wait, account.name,
        )
        await asyncio.sleep(fallback_wait)
    elif not found:
        logger.error(
            "Auto-login skipped for %s: login screen not detected within %.0fs (handle=%s)",
            account.name, timeout, handle,
        )
        return False
    else:
        logger.info("Login screen detected for %s (handle=%s)", account.name, handle)
        await asyncio.sleep(0.4)  # let the username field take focus

    await send_login(handle, account.username, account.password)
    logger.info("Credentials sent for %s (handle=%s)", account.name, handle)
    apply_window_options(config, handle, account)

    result = "unknown"
    if found:
        result = await _wait_for_login_result(handle)
    if result == "error":
        logger.error(
            "Login FAILED for %s: the game shows an error popup "
            "(wrong username/password?)", account.name,
        )
    elif result == "still_login":
        logger.warning(
            "%s is still on the login screen after pressing Enter", account.name
        )
    elif result == "ok":
        logger.info("Login went through for %s (no error popup)", account.name)
    return result not in ("error", "still_login")


async def start_instances_with_login(
    instance_number: int, accounts: List[Account], config: dict
) -> None:
    """Start N instances and auto-login selected accounts once their login screens are up."""
    logger = logging.getLogger("launcher")
    start_handles = set(get_wizard_handles_safe())

    for _ in range(instance_number):
        start_instance(config)

    # The game window can take a while to appear: poll instead of a single snapshot.
    expected = min(instance_number, len(accounts))
    deadline = time.monotonic() + float(config.get("handle_wait_seconds", 60))
    new_handles: Set[int] = set()
    while time.monotonic() < deadline:
        new_handles = set(get_wizard_handles_safe()).difference(start_handles)
        if len(new_handles) >= expected:
            break
        await asyncio.sleep(0.2)

    if not new_handles:
        logger.error("No Wizard101 window appeared - auto-login aborted")
        return
    if len(new_handles) < expected:
        logger.warning(
            "Only %s/%s Wizard101 windows appeared", len(new_handles), expected
        )

    results = await asyncio.gather(
        *(
            login_when_ready(handle, account, config)
            for handle, account in zip(sorted(new_handles), accounts)
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            logger.error("Auto-login error: %r", result)


def launch_with_login(accounts: List[Account], count: int, config: dict) -> None:
    """Start launcher thread with auto-login."""
    logger = logging.getLogger("launcher")

    def runner() -> None:
        try:
            asyncio.run(start_instances_with_login(count, accounts, config))
            logger.info("Auto-login finished for %s instances", count)
        except Exception as exc:
            logger.exception("Auto-login failed: %s", exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()



def track_session_start(tracker: PlaytimeTracker, handle: int, username: str) -> None:
    """Start tracking a playtime session."""
    tracker.start_session(handle, username)
    logging.getLogger("launcher").info(
        "Playtime session started: username=%s handle=%s",
        username,
        handle,
    )


def track_session_end(
    tracker: PlaytimeTracker,
    handle: int,
    account: Account,
    accounts: List[Account],
    reason: str = "process_exit",
) -> Optional[float]:
    """End tracking and update account playtime."""
    duration = tracker.end_session(handle)
    if duration > 0:
        account = tracker.update_account_playtime(account, duration)
        # Update account in list
        for i, acc in enumerate(accounts):
            if acc.username == account.username:
                accounts[i] = account
                break
        logging.getLogger("launcher").info(
            "Playtime session ended (%s): account=%s username=%s handle=%s duration=%s",
            reason,
            account.name,
            account.username,
            handle,
            tracker.format_playtime(duration),
        )
        return duration
    return None
