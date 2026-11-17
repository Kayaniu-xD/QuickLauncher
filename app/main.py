"""
Wizard101 Launcher - Main Application Entry Point

This is the main event loop and application controller. It handles:
- User interactions and events from the UI
- Account management (add, edit, delete accounts)
- Launcher operations (quicklaunch, multi-instance, auto-login)
- Region switching and configuration
- Playtime tracking
- Configuration persistence

Architecture:
- ui.py: UI components and layouts
- config.py: Configuration management
- crypto.py: Account encryption/decryption
- launcher.py: Game launching logic
- playtime_tracker.py: Session tracking
- logging_utils.py: Logger setup
- performance_monitor.py: System monitoring
- discord_integration.py: Discord webhook notifications
- log_viewer.py: Live log file reading
- update_checker.py: Version checking
- issue_reporter.py: Error reporting

"""

import logging
import os
import sys
import time
import psutil
import shutil
import subprocess
import tempfile
import webbrowser
from pathlib import Path

import PySimpleGUI as sg

from core.config import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_CONFIG,
    DEFAULT_GITHUB_REPO,
    PUBLISHERS,
    REGION_META,
    load_config,
    save_config,
)
from security.backup import BackupError, run_export, run_import
from security.crypto import (
    Account,
    account_display,
    add_edit_account,
    change_master_password,
    change_master_password_dialog,
    load_accounts,
    load_master_password,
    save_accounts,
)
from services.launcher import (
    WIZWALKER_AVAILABLE,
    launch_with_login,
    start_instance,
    track_session_start,
    track_session_end,
    get_wizard_handles_safe,
)
from core.logging_utils import setup_logging
from services.playtime_tracker import PlaytimeTracker
from app.ui import apply_theme, build_window, handle_nav_event, resolve_icon_path
from services.performance_monitor import PerformanceMonitor
from integrations.discord_integration import DiscordIntegration
from integrations.discord_presence import DiscordRichPresence, RollingActivity24h
from services.log_viewer import LogViewer
from services.update_checker import UpdateChecker
from services.git_sync import GitError, GitSync
from services.issue_reporter import IssueReporter
from services.window_manager import WindowManager


def get_paths() -> dict:
    """
    Get all important application directory paths.
    
    Handles both:
    - Release mode: When running as frozen .exe (PyInstaller)
    - Development mode: When running as .py script
    
    Returns:
        Dictionary with application paths:
            - root: Application (install) directory - read-only in Program Files
            - data_dir: Everything the app writes: config, accounts, exports
            - log_dir: For log files
            - config_file: config.json path (inside data_dir)
            - accounts_file: accounts.enc.json path (inside data_dir)

    Only data_dir and log_dir need to be writable, so the installer grants
    write access to just those two folders and keeps the .exe itself protected.
    """
    if getattr(sys, "frozen", False):
        # Running as compiled .exe (PyInstaller)
        root = Path(sys.executable).resolve().parent
    else:
        # Running as Python script
        root = Path(__file__).resolve().parent.parent
    
    data_dir = root / "data"
    log_dir = root / "logs"
    
    return {
        "root": root,
        "data_dir": data_dir,
        "log_dir": log_dir,
        "config_file": data_dir / "config.json",
        "accounts_file": data_dir / "accounts.enc.json",
    }


def ensure_dirs(paths: dict) -> None:
    """
    Create required application directories if they don't exist.
    
    Args:
        paths: Dictionary from get_paths()
    """
    paths["data_dir"].mkdir(parents=True, exist_ok=True)
    paths["log_dir"].mkdir(parents=True, exist_ok=True)

    # Older versions kept config.json next to the exe: carry it over once.
    legacy_config = paths["root"] / "config.json"
    if legacy_config.exists() and not paths["config_file"].exists():
        try:
            shutil.copy2(legacy_config, paths["config_file"])
        except OSError:
            pass


def run_update_check(config: dict) -> dict:
    """Ask GitHub for the latest release (worker thread)."""
    checker = UpdateChecker(APP_VERSION, repo=config.get("github_repo", DEFAULT_GITHUB_REPO))
    return checker.check_status()


def download_update(url: str) -> dict:
    """Download the release installer into a temp folder (worker thread)."""
    try:
        path = UpdateChecker.download_installer(url, Path(tempfile.gettempdir()) / "QuickLauncherUpdate")
        return {"ok": True, "path": str(path)}
    except Exception as exc:  # network / disk / refused URL
        return {"ok": False, "msg": f"Download failed: {exc}"}


def git_action(action) -> dict:
    """Run one GitSync action and turn the outcome into {"ok", "msg"} (worker thread)."""
    try:
        return {"ok": True, "msg": action()}
    except GitError as exc:
        return {"ok": False, "msg": str(exc)}
    except Exception as exc:
        return {"ok": False, "msg": f"Unexpected error: {exc}"}


def git_status_payload(git_sync: GitSync) -> dict:
    """Everything the Git sync card shows (worker thread; fetches from the remote)."""
    try:
        info = git_sync.status(fetch=True)
        return {"ok": True, "text": GitSync.describe(info), "remote": info["remote_url"],
                "autopush": git_sync.auto_push_enabled()}
    except Exception as exc:
        return {"ok": False, "text": f"Git status failed: {exc}", "remote": "", "autopush": False}


def set_update_status(window, text: str, good: bool = True, neutral: bool = False) -> None:
    """Colour-coded text on the Update page (silently ignored if the page doesn't exist)."""
    color = "#9A9CA6" if neutral else ("#3DDC97" if good else "#FF5C7A")
    try:
        window["-UPD-STATUS-"].update(text, text_color=color)
    except Exception:
        pass


def get_region_accounts(accounts: list, current_region: str) -> list:
    """
    Filter accounts by region.
    
    Args:
        accounts: List of all Account objects
        current_region: Current region code (e.g., 'de', 'us')
        
    Returns:
        List of Account objects for the current region
    """
    return [acc for acc in accounts if acc.region == current_region]


def get_account_by_username(accounts: list, username: str) -> Account:
    """Find account object by username."""
    return next((acc for acc in accounts if acc.username == username), None)


def build_active_account_map(active_sessions: dict, accounts: list) -> dict:
    """Build handle -> account display mapping for window manager UI."""
    account_map = {}
    for handle, username in active_sessions.items():
        account = get_account_by_username(accounts, username)
        account_map[handle] = account.name if account else username
    return account_map


def detect_new_handles(
    before_handles: set,
    expected_count: int,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.2,
) -> list:
    """Poll for newly started Wizard101 handles until timeout or expected count is reached."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        new_handles = sorted(set(get_wizard_handles_safe()).difference(before_handles))
        if len(new_handles) >= expected_count:
            return new_handles
        time.sleep(poll_interval_seconds)

    return sorted(set(get_wizard_handles_safe()).difference(before_handles))



def get_selected_account_for_presence(
    values: dict,
    accounts: list,
    config: dict,
    active_sessions: dict,
) -> Account:
    """Resolve the currently selected account for Discord presence session display."""
    current_region = config.get("current_region", "de")
    region_accounts = get_region_accounts(accounts, current_region)

    launch_selected = values.get("-AUTO-ACCOUNTS-", []) if values else []
    if launch_selected:
        account = next(
            (acc for acc in region_accounts if account_display(acc) in launch_selected),
            None,
        )
        if account:
            return account

    accounts_selected = values.get("-ACCOUNTS-", []) if values else []
    if accounts_selected:
        account = next(
            (acc for acc in region_accounts if account_display(acc) in accounts_selected),
            None,
        )
        if account:
            return account

    if active_sessions:
        first_username = next(iter(active_sessions.values()))
        return get_account_by_username(accounts, first_username)

    return None


def get_active_username_session_seconds(tracker: PlaytimeTracker, username: str) -> float:
    """Get current active session duration for username (max if multiple handles exist)."""
    if not username:
        return 0.0

    now = time.time()
    durations = [
        max(0.0, now - start_time)
        for start_time, session_username in tracker.active_sessions.values()
        if session_username == username
    ]
    return max(durations) if durations else 0.0


def sync_discord_presence_config(
    discord_presence: DiscordRichPresence,
    config: dict,
) -> None:
    """Apply runtime config updates to the Discord Rich Presence client."""
    discord_presence.enabled = bool(config.get("discord_rich_presence", True))

    try:
        discord_presence.update_interval_seconds = max(
            5,
            int(config.get("discord_rpc_update_interval", 15)),
        )
    except (TypeError, ValueError):
        discord_presence.update_interval_seconds = 15

    client_id = str(config.get("discord_rpc_client_id", "")).strip()
    env_client_id = os.getenv("WIZ_DISCORD_RPC_CLIENT_ID", "").strip()
    effective_client_id = client_id or env_client_id

    if discord_presence.client_id != effective_client_id:
        discord_presence.close()
        discord_presence.client_id = effective_client_id
def launch_and_detect(config: dict, selected_accounts: list, count: int) -> dict:
    """
    Start the game instances (with auto-login) and wait for their windows to appear.

    This blocks for as long as the game needs to open a window (several seconds), so it
    must run in a worker thread (window.perform_long_operation), never in the UI thread.
    Returns the new window handles for register_started_sessions().
    """
    logger = logging.getLogger("launcher")
    accounts_to_start = selected_accounts[:count]

    # Window handles that exist before the launch, to recognise the new ones afterwards
    before_handles = set(get_wizard_handles_safe())

    # Start the instances (the login itself continues in its own thread)
    launch_with_login(accounts_to_start, count, config)

    # Poll for new handles because client startup time can vary significantly.
    detection_timeout = float(config.get("handle_detect_timeout_seconds", 15))
    new_handles = detect_new_handles(before_handles, count, detection_timeout)

    if not new_handles:
        logger.warning("No new Wizard101 handles detected for playtime tracking")
    elif len(new_handles) < count:
        logger.warning(
            "Detected only %s/%s new handles; tracking available sessions only",
            len(new_handles),
            count,
        )
    return {
        "handles": list(new_handles),
        "usernames": [acc.username for acc in accounts_to_start],
        "count": count,
    }


def register_started_sessions(
    result: dict,
    config: dict,
    tracker: PlaytimeTracker,
    active_sessions: dict,
    accounts: list,
    discord: DiscordIntegration = None,
) -> int:
    """Start playtime tracking for the windows found by launch_and_detect (UI thread)."""
    logger = logging.getLogger("launcher")
    started = 0
    for handle, username in zip(result.get("handles", []), result.get("usernames", [])):
        account = get_account_by_username(accounts, username)
        if account is None:
            continue
        track_session_start(tracker, handle, account.username)
        active_sessions[handle] = account.username
        started += 1
        logger.info(
            "Playtime session started: account=%s username=%s handle=%s",
            account.name,
            account.username,
            handle,
        )

        if discord and config.get("discord_notifications"):
            discord.send_session_started(
                account.name,
                account.username,
                config.get("current_region", "de"),
            )
    return started


def start_and_track_sessions(
    config: dict,
    selected_accounts: list,
    count: int,
    tracker: PlaytimeTracker,
    active_sessions: dict,
    paths: dict,
    master_password: str,
    accounts: list,
    discord: DiscordIntegration = None,
) -> None:
    """Blocking variant (launch, wait for the windows, track). The UI uses begin_auto_login()."""
    result = launch_and_detect(config, selected_accounts, count)
    register_started_sessions(result, config, tracker, active_sessions, accounts, discord)


def main() -> int:
    """
    Main application entry point and event loop controller.
    
    Initializes:
    - Configuration system
    - Logging
    - UI theme
    - Accounts and master password
    - Playtime tracking
    - Performance monitoring
    - Discord integration
    - Update checking
    
    Event loop handles:
    - Account management (add/edit/delete)
    - Launcher operations (quicklaunch, multi-instance, auto-login)
    - Region switching
    - UI interactions (log viewer, theme toggle, etc.)
    - Playtime tracking
    - Discord notifications
    
    Returns:
        Exit code (0 for success)
    """
    # Initialize paths and directories
    paths = get_paths()
    ensure_dirs(paths)
    
    # Load configuration
    config = load_config(paths["config_file"])
    
    # Setup logging system
    setup_logging(paths["log_dir"], config)
    
    # Apply UI theme
    apply_theme(config)

    # One icon for every window, including the master-password prompt shown below
    app_icon = resolve_icon_path(config)
    if app_icon:
        sg.set_global_icon(app_icon)
    
    # Get logger for this module
    logger = logging.getLogger("launcher")
    
    # Load master password and accounts
    master_password = load_master_password(paths["accounts_file"])
    if not master_password:
        return 1

    try:
        accounts = load_accounts(paths["accounts_file"], master_password)
    except Exception as exc:
        sg.popup(f"Failed to load accounts: {exc}", title=APP_NAME)
        accounts = []

    # Initialize core systems
    tracker = PlaytimeTracker()
    active_sessions = {}  # {handle: account_username}

    # Initialize rolling 24h activity tracker + Discord Rich Presence
    activity_24h = RollingActivity24h(paths["data_dir"] / "activity_24h.json")
    discord_presence = DiscordRichPresence(
        client_id=str(config.get("discord_rpc_client_id", "")).strip(),
        enabled=bool(config.get("discord_rich_presence", True)),
        update_interval_seconds=int(config.get("discord_rpc_update_interval", 15)),
    )
    sync_discord_presence_config(discord_presence, config)
    
    # Initialize performance monitor
    try:
        perf_monitor = PerformanceMonitor(max_history=100)
        perf_monitor.logger.setLevel(config.get("log_level", "INFO"))
    except Exception as e:
        logger.warning(f"Failed to initialize performance monitor: {e}")
        perf_monitor = None
    
    # Initialize Discord integration
    try:
        discord = DiscordIntegration(webhook_url=config.get("discord_webhook_url"))
        discord.enabled = config.get("discord_notifications", False)
    except Exception as e:
        logger.warning(f"Failed to initialize Discord integration: {e}")
        discord = None
    
    # Initialize update checker
    try:
        updater = UpdateChecker(current_version=APP_VERSION, repo=config.get("github_repo", DEFAULT_GITHUB_REPO))
    except Exception as e:
        logger.warning(f"Failed to initialize update checker: {e}")
        updater = None
    
    # Initialize error reporter
    try:
        reporter = IssueReporter(
            github_repo=config.get("github_repo", DEFAULT_GITHUB_REPO),
            discord_webhook=config.get("discord_webhook_url")
        )
    except Exception as e:
        logger.warning(f"Failed to initialize issue reporter: {e}")
        reporter = None
    
    # Initialize log viewer
    try:
        log_file = paths["log_dir"] / "launcher.log"
        log_viewer = LogViewer(log_file) if log_file.exists() else None
    except Exception as e:
        logger.warning(f"Failed to initialize log viewer: {e}")
        log_viewer = None
    
    # Initialize window manager
    try:
        win_manager = WindowManager()
    except Exception as e:
        logger.warning(f"Failed to initialize window manager: {e}")
        win_manager = None
    
    # Build and display window
    try:
        window = build_window(config, accounts, log_file_path=str(log_file))
    except Exception as e:
        logger.exception(f"Failed to build window: {e}")
        sg.popup_error(f"Failed to build window:\n{str(e)}", title=APP_NAME)
        return 1
    logger = logging.getLogger("launcher")

    # Git checkout (source install) or None (installed exe): enables the Git sync card
    git_sync = GitSync.for_app(paths["root"])
    pending_update = None  # update info from the last successful check
    auto_login_busy = {"on": False}  # True while a launch is waiting for its game windows

    def begin_auto_login(win, vals, cfg, chosen_accounts, source):
        """Start the game + auto-login in a worker thread so the window never freezes."""
        if auto_login_busy["on"]:
            win["-STATUS-"].update("Still starting the previous instances, one moment...")
            return
        count = len(chosen_accounts)
        cfg["foreground_on_login"] = vals.get("-FOREGROUND-", True)
        cfg["set_window_title"] = vals.get("-SETTITLE-", True)
        auto_login_busy["on"] = True
        win["-STATUS-"].update(f"Starting {count} account(s) with auto-login...")
        chosen = list(chosen_accounts)
        win.perform_long_operation(
            lambda: launch_and_detect(dict(cfg), chosen, count), "-AUTO-LAUNCH-DONE-")

    # Start-up tasks run in the background so the window opens immediately
    if git_sync and config.get("git_auto_pull"):
        window.perform_long_operation(lambda: git_action(git_sync.pull), "-GIT-AUTOPULL-DONE-")
    if config.get("check_for_updates", True):
        window.perform_long_operation(lambda: run_update_check(config), "-UPD-CHECK-DONE-")

    while True:
        try:
            event, values = window.read(timeout=1000)  # 1 second timeout for periodic checks
        except Exception as e:
            logger.exception(f"CRASH in window.read(): {e}")
            sg.popup_error(f"Window Error:\n{str(e)}", title=APP_NAME)
            break
        
        # Handle playtime polling (timeout events)
        if event == sg.TIMEOUT_EVENT:
            try:
                # Check for closed handles and end sessions
                if active_sessions:
                    current_handles = set(get_wizard_handles_safe())
                    closed_handles = [h for h in active_sessions if h not in current_handles]
                    for handle in closed_handles:
                        acc_username = active_sessions.pop(handle)
                        acc = get_account_by_username(accounts, acc_username)
                        if acc:
                            track_session_end(
                                tracker,
                                handle,
                                acc,
                                accounts,
                                reason="window_closed",
                            )
                            save_accounts(paths["accounts_file"], master_password, accounts)
                        else:
                            logger.warning(
                                "Playtime session ended for unknown username=%s handle=%s",
                                acc_username,
                                handle,
                            )
                sync_discord_presence_config(discord_presence, config)
                activity_24h.set_active(bool(active_sessions))

                selected_account = get_selected_account_for_presence(
                    values,
                    accounts,
                    config,
                    active_sessions,
                )
                selected_name = selected_account.name if selected_account else None
                selected_seconds = get_active_username_session_seconds(
                    tracker,
                    selected_account.username if selected_account else "",
                )

                discord_presence.update_presence(
                    total_24h_seconds=activity_24h.get_total_last_24h(),
                    selected_account_name=selected_name,
                    selected_session_seconds=selected_seconds,
                    active_sessions_count=len(active_sessions),
                    region=config.get("current_region", "de"),
                )
            except Exception as e:
                logger.exception(f"CRASH in TIMEOUT_EVENT handler: {e}")
            continue

        if event in (sg.WIN_CLOSED, "Exit"):
            break

        # ---------------- Update page: releases + git sync ----------------
        if event == "-UPD-CHECK-":
            set_update_status(window, "Checking for updates...", neutral=True)
            window.perform_long_operation(lambda: run_update_check(config), "-UPD-CHECK-DONE-")
            continue

        if event == "-UPD-CHECK-DONE-":
            result = values[event]
            pending_update = result["info"]
            set_update_status(window, result["message"], good=result["state"] != "error",
                              neutral=result["state"] in ("none", "current"))
            try:
                window["-NAVCHIP-update-"].update("NEW" if result["state"] == "update" else "")
            except Exception:
                pass
            if result["state"] == "update":
                window["-STATUS-"].update(f"✓ {result['message']} (open the Update page)")
            continue

        if event == "-UPD-NOW-":
            if git_sync:
                set_update_status(window, "Pulling from git...", neutral=True)
                window.perform_long_operation(lambda: git_action(git_sync.pull), "-GIT-DONE-")
            elif pending_update and str(pending_update.get("download_url", "")).lower().endswith(".exe"):
                answer = sg.popup_yes_no(
                    f"Download QuickLauncher v{pending_update['new_version']} and run the installer?\n"
                    "QuickLauncher will close; your accounts and settings are kept.",
                    title=APP_NAME,
                )
                if answer == "Yes":
                    set_update_status(window, "Downloading the installer...", neutral=True)
                    url = pending_update["download_url"]
                    window.perform_long_operation(lambda: download_update(url), "-UPD-DL-DONE-")
            elif pending_update and pending_update.get("download_url"):
                webbrowser.open(pending_update["download_url"])  # release without an installer asset
                set_update_status(window, "Opened the release page in your browser.")
            else:
                set_update_status(window, "Checking for updates...", neutral=True)
                window.perform_long_operation(lambda: run_update_check(config), "-UPD-CHECK-DONE-")
            continue

        if event == "-UPD-DL-DONE-":
            result = values[event]
            if not result["ok"]:
                set_update_status(window, result["msg"], good=False)
                continue
            try:
                subprocess.Popen([result["path"]], close_fds=True,
                                 env=UpdateChecker.fresh_process_env())  # the wizard closes/relaunches us
            except OSError as exc:
                set_update_status(window, f"Could not start the installer: {exc}", good=False)
                continue
            logger.info("Update installer started: %s", result["path"])
            sg.popup("The installer has started.\nQuickLauncher will now close.", title=APP_NAME)
            break

        if isinstance(event, str) and event.startswith("-OPEN-PUB-"):
            index = event[len("-OPEN-PUB-"):-1]
            if index.isdigit() and int(index) < len(PUBLISHERS):
                webbrowser.open(PUBLISHERS[int(index)][1])
            continue

        if event == "-UPD-AUTOCHECK-":
            config["check_for_updates"] = bool(values.get("-UPD-AUTOCHECK-"))
            save_config(paths["config_file"], config)
            continue

        if event == "-GIT-AUTOPULL-DONE-":
            result = values[event]
            window["-STATUS-"].update(("Git: " if result["ok"] else "Git pull failed: ") + result["msg"])
            if result["ok"] and not result["msg"].startswith("Already"):
                sg.popup("QuickLauncher was updated from git.\nRestart it to use the new version.", title=APP_NAME)
            continue

        if git_sync and event == "-GIT-REFRESH-":
            window["-GIT-STATUS-"].update("Loading git status...")
            window.perform_long_operation(lambda: git_status_payload(git_sync), "-GIT-REFRESH-DONE-")
            continue

        if event == "-GIT-REFRESH-DONE-":
            result = values[event]
            window["-GIT-STATUS-"].update(result["text"], text_color="#9A9CA6" if result["ok"] else "#FF5C7A")
            window["-GIT-REMOTE-"].update(result["remote"])
            window["-GIT-AUTOPUSH-"].update(result["autopush"])
            continue

        if git_sync and event == "-GIT-SET-REMOTE-":
            url = values.get("-GIT-REMOTE-", "")
            window.perform_long_operation(lambda: git_action(lambda: git_sync.set_remote(url)), "-GIT-DONE-")
            continue

        if git_sync and event in ("-GIT-PULL-", "-GIT-PUSH-"):
            action = git_sync.pull if event == "-GIT-PULL-" else git_sync.push
            window["-GIT-STATUS-"].update("Working...", text_color="#9A9CA6")
            window.perform_long_operation(lambda: git_action(action), "-GIT-DONE-")
            continue

        if git_sync and event == "-GIT-COMMIT-PUSH-":
            message = sg.popup_get_text("Commit message:", title=APP_NAME,
                                        default_text="Update from QuickLauncher")
            if message is not None:
                window["-GIT-STATUS-"].update("Committing and pushing...", text_color="#9A9CA6")
                window.perform_long_operation(
                    lambda: git_action(lambda: git_sync.commit_and_push(message, bump_version=True)), "-GIT-DONE-")
            continue

        if event == "-GIT-DONE-":
            result = values[event]
            set_update_status(window, result["msg"], good=result["ok"])
            window["-STATUS-"].update(("Git: " if result["ok"] else "Git failed: ") + result["msg"])
            if not result["ok"] and "-GIT-STATUS-" in window.AllKeysDict:
                window["-GIT-STATUS-"].update(result["msg"], text_color="#FF5C7A")
            elif git_sync:
                window.write_event_value("-GIT-REFRESH-", None)
            continue

        if event == "-GIT-AUTOPULL-":
            config["git_auto_pull"] = bool(values.get("-GIT-AUTOPULL-"))
            save_config(paths["config_file"], config)
            continue

        if git_sync and event == "-GIT-AUTOPUSH-":
            wanted = bool(values.get("-GIT-AUTOPUSH-"))
            try:
                git_sync.set_auto_push(wanted)
                config["git_auto_push"] = wanted
                save_config(paths["config_file"], config)
                window["-STATUS-"].update(f"Git: auto-push {'on' if wanted else 'off'}")
            except (GitError, OSError) as exc:
                window["-GIT-AUTOPUSH-"].update(not wanted)
                sg.popup_error(f"Could not change auto-push:\n{exc}", title=APP_NAME)
            continue

        # Category list / search on the left "book" page
        if handle_nav_event(window, event, values):
            if isinstance(event, str) and "update" in event and event.startswith("-NAV") and git_sync:
                window.write_event_value("-GIT-REFRESH-", None)  # opening the page loads fresh status
            continue

        # Export / import settings + accounts as one encrypted file
        if event == "-EXPORT-BACKUP-":
            try:
                message = run_export(config, accounts, master_password)
                if message:
                    window["-STATUS-"].update(f"✓ {message}")
            except BackupError as exc:
                logger.warning("Backup export failed: %s", exc)
                sg.popup_error(str(exc), title=APP_NAME)
            continue

        if event == "-IMPORT-BACKUP-":
            try:
                result = run_import(config, accounts)
            except BackupError as exc:
                logger.warning("Backup import failed: %s", exc)
                sg.popup_error(str(exc), title=APP_NAME)
                continue
            if result:
                try:
                    config.clear()
                    config.update(result["config"])
                    accounts[:] = result["accounts"]
                    save_config(paths["config_file"], config)
                    save_accounts(paths["accounts_file"], master_password, accounts)
                except OSError as exc:
                    sg.popup_error(f"The import could not be saved:\n{exc}", title=APP_NAME)
                    continue
                apply_theme(config)
                window.close()
                window = build_window(config, accounts, log_file_path=str(log_file))
                window["-STATUS-"].update(f"✓ {result['message']}")
                logger.info(result["message"])
            continue

        # Change the master password (needs the current one)
        if event == "-CHANGE-MASTER-":
            new_master = change_master_password_dialog(master_password)
            if new_master:
                try:
                    change_master_password(paths["accounts_file"], accounts, new_master)
                    master_password = new_master
                    window["-STATUS-"].update("✓ Master password changed")
                    logger.info("Master password changed")
                    sg.popup("Master password changed.\nUse the new one next time you start the launcher.",
                             title=APP_NAME)
                except Exception as exc:
                    logger.exception("Changing the master password failed")
                    sg.popup_error(f"Master password NOT changed (your old one still works):\n{exc}",
                                   title=APP_NAME)
            continue

        # Apply Region - switch current region from dropdown
        if event == "-APPLY-REGION-":
            try:
                selected_region_display = values.get("-REGION-SELECT-", "")
                if selected_region_display:
                    # Extract region code from display (e.g., "DE - Deutschland" -> "de")
                    region_code = selected_region_display.split(" - ")[0].lower()
                    if region_code in REGION_META:
                        config["current_region"] = region_code
                        save_config(paths["config_file"], config)

                        window.close()
                        window = build_window(config, accounts)
                        region_name = REGION_META[region_code]["name"]
                        window["-STATUS-"].update(f"✓ Switched to {region_name}")
                        logger.info(f"Region switched to {region_code}")
            except Exception as e:
                logger.exception(f"CRASH in APPLY-REGION: {e}")
                sg.popup_error(f"Region switch failed:\n{str(e)}", title=APP_NAME)
        
        # Update selected accounts counter
        if event == "-AUTO-ACCOUNTS-":
            try:
                selected_count = len(values.get("-AUTO-ACCOUNTS-", []))
                plural = "account" if selected_count == 1 else "accounts"
                window["-SELECTED-COUNT-"].update(f"{selected_count} {plural} selected")
            except Exception as e:
                logger.exception(f"Failed to update selected count: {e}")
        
        if event == "Quicklaunch":
            try:
                start_instance(config)
                window["-STATUS-"].update("Quicklaunch started")
            except Exception as exc:
                sg.popup(f"Launch failed: {exc}", title=APP_NAME)

        # Start Instances - start one instance for each selected account (without login)
        if event == "Start Instances":
            try:
                selected = values.get("-AUTO-ACCOUNTS-", [])
                region_accounts = get_region_accounts(accounts, config.get("current_region", "de"))
                selected_accounts = [
                    acc for acc in region_accounts if account_display(acc) in selected
                ]
                if not selected_accounts:
                    sg.popup("Select at least one account to start instances", title=APP_NAME)
                    continue
                
                count = len(selected_accounts)
                for _ in range(count):
                    start_instance(config)
                window["-STATUS-"].update(f"Started {count} instance(s) (no login)")
            except Exception as exc:
                sg.popup(f"Launch failed: {exc}", title=APP_NAME)

        # Start + Auto Login - start all selected accounts with auto-login
        if event == "Start + Auto Login":
            if not WIZWALKER_AVAILABLE:
                sg.popup("wizwalker not installed", title=APP_NAME)
                continue

            selected = values.get("-AUTO-ACCOUNTS-", [])
            region_accounts = get_region_accounts(accounts, config.get("current_region", "de"))
            selected_accounts = [
                acc for acc in region_accounts if account_display(acc) in selected
            ]
            if not selected_accounts:
                sg.popup("Select at least one account to start with auto-login", title=APP_NAME)
                continue

            begin_auto_login(window, values, config, selected_accounts, "button")

        # Game windows found after "Start + Auto Login" / F3: start playtime tracking
        if event == "-AUTO-LAUNCH-DONE-":
            auto_login_busy["on"] = False
            result = values[event]
            if isinstance(result, dict):
                started = register_started_sessions(
                    result, config, tracker, active_sessions, accounts, discord)
                window["-STATUS-"].update(
                    f"Started {result['count']} account(s) with auto-login + tracking"
                    if started else "Launched, but no game window was detected for tracking")
            else:
                window["-STATUS-"].update("Auto-login launch failed - see the log")
            continue

        # Add account
        if event == "Add":
            current_region = config.get("current_region", "de")
            new_account = add_edit_account(current_region=current_region)
            if new_account:
                accounts.append(new_account)
                save_accounts(paths["accounts_file"], master_password, accounts)
                
                # Update UI with region-filtered accounts
                region_accounts = get_region_accounts(accounts, current_region)
                window["-ACCOUNTS-"].update([account_display(a) for a in region_accounts])
                window["-AUTO-ACCOUNTS-"].update([account_display(a) for a in region_accounts])
                window["-SELECTED-COUNT-"].update("0 accounts selected")

        # Edit account
        if event == "Edit":
            current_region = config.get("current_region", "de")
            region_accounts = get_region_accounts(accounts, current_region)
            selected = values.get("-ACCOUNTS-", [])
            if not selected:
                sg.popup("Select an account", title=APP_NAME)
                continue
            current = next(
                (acc for acc in region_accounts if account_display(acc) in selected),
                None,
            )
            if not current:
                continue
            updated = add_edit_account(current, current_region=current_region)
            if updated:
                idx = accounts.index(current)
                accounts[idx] = updated
                save_accounts(paths["accounts_file"], master_password, accounts)
                
                # Update UI with region-filtered accounts
                region_accounts = get_region_accounts(accounts, current_region)
                window["-ACCOUNTS-"].update([account_display(a) for a in region_accounts])
                window["-AUTO-ACCOUNTS-"].update([account_display(a) for a in region_accounts])
                window["-SELECTED-COUNT-"].update("0 accounts selected")

        # Delete account
        if event == "Delete":
            current_region = config.get("current_region", "de")
            region_accounts = get_region_accounts(accounts, current_region)
            selected = values.get("-ACCOUNTS-", [])
            if not selected:
                sg.popup("Select an account", title=APP_NAME)
                continue
            if sg.popup_yes_no("Delete selected account?", title=APP_NAME) != "Yes":
                continue
            
            # Delete from all accounts (not just region_accounts)
            accounts = [acc for acc in accounts if account_display(acc) not in selected]
            save_accounts(paths["accounts_file"], master_password, accounts)
            
            # Update UI with region-filtered accounts
            region_accounts = get_region_accounts(accounts, current_region)
            window["-ACCOUNTS-"].update([account_display(a) for a in region_accounts])
            window["-AUTO-ACCOUNTS-"].update([account_display(a) for a in region_accounts])
            window["-SELECTED-COUNT-"].update("0 accounts selected")

        # Handle region visibility toggle
        region_visibility_changed = False
        for region_code in REGION_META:
            toggle_key = f"-SHOW-{region_code.upper()}-"
            if event == toggle_key:
                config[f"show_region_{region_code}"] = values.get(toggle_key, False)
                region_visibility_changed = True

        # Save settings and handle region changes
        if event == "Save settings" or region_visibility_changed:
            # Save global settings
            try:
                config["login_wait_seconds"] = float(values.get("-WAIT-", 5))
            except ValueError:
                config["login_wait_seconds"] = 5
            config["window_title_template"] = values.get(
                "-TITLE-", "{name} ({username})"
            )
            config["log_level"] = values.get("-LOGLEVEL-", "INFO")
            config["discord_rich_presence"] = values.get("-DISCORD-RPC-", True)
            config["discord_rpc_client_id"] = values.get("-DISCORD-RPC-ID-", "").strip()
            try:
                config["discord_rpc_update_interval"] = max(5, int(values.get("-DISCORD-RPC-INTERVAL-", 15)))
            except ValueError:
                config["discord_rpc_update_interval"] = 15
            
            # Save region visibility settings
            for region_code in REGION_META:
                toggle_key = f"-SHOW-{region_code.upper()}-"
                config[f"show_region_{region_code}"] = values.get(toggle_key, False)
            
            save_config(paths["config_file"], config)
            
            if region_visibility_changed:

                window.close()
                window = build_window(config, accounts)
                window["-STATUS-"].update("Regions updated, window refreshed")
            else:
                window["-STATUS-"].update("Settings saved")

        # ================== EXTENSIONS / WIZWALL ==================
        # Toggle Wizwall Extension
        if event == "-WIZWALL-ENABLED-":
            wizwall_enabled = values["-WIZWALL-ENABLED-"]
            config["wizwall_enabled"] = wizwall_enabled
            
            # Enable/disable all wizwall controls
            window["-WIZWALL-LAYOUT-"].update(disabled=not wizwall_enabled)
            window["-WIZWALL-AUTO-RESOLUTION-"].update(disabled=not wizwall_enabled)
            window["-ARRANGE-WINDOWS-"].update(disabled=not wizwall_enabled)
            window["-REFRESH-WINDOWS-"].update(disabled=not wizwall_enabled)
            window["-SAVE-LAYOUT-"].update(disabled=not wizwall_enabled)
            window["-RESTORE-LAYOUT-"].update(disabled=not wizwall_enabled)
            window["-SET-RESOLUTION-"].update(disabled=not wizwall_enabled)
            
            status = "enabled" if wizwall_enabled else "disabled"
            window["-STATUS-"].update(f"Wizwall extension {status}")
            save_config(paths["config_file"], config)
        
        # Set Resolution for Layout
        if event == "-SET-RESOLUTION-" and win_manager:
            try:
                layout = values.get("-WIZWALL-LAYOUT-", "2x2")
                width, height = win_manager.get_optimal_resolution_for_layout(layout)
                
                # Get wizard install path
                from services.launcher import get_wiz_install
                wiz_path = get_wiz_install(config)
                
                # Confirm with user
                msg = (
                    f"This will set the game resolution to {width}x{height}\n"
                    f"for optimal {layout} grid layout.\n\n"
                    f"File: {wiz_path}/Bin/preferences.xml\n\n"
                    f"⚠️  All running Wizard101 clients must be RESTARTED\n"
                    f"for this change to take effect!\n\n"
                    f"Continue?"
                )
                
                if sg.popup_yes_no(msg, title="Set Game Resolution") == "Yes":
                    success = win_manager.set_game_resolution(width, height, str(wiz_path))
                    if success:
                        sg.popup_ok(
                            f"✓ Resolution set to {width}x{height}\n\n"
                            f"Please RESTART all Wizard101 clients now!",
                            title="Success"
                        )
                        window["-STATUS-"].update(f"Set resolution: {width}x{height} (restart clients!)")
                    else:
                        sg.popup_error("Failed to set resolution. Check logs.", title=APP_NAME)
            except Exception as e:
                logger.exception(f"Set resolution failed: {e}")
                sg.popup_error(f"Failed to set resolution:\n{str(e)}", title=APP_NAME)
        
        # Arrange Windows - Multi-window grid layout
        if event == "-ARRANGE-WINDOWS-" and win_manager:
            try:
                layout = values.get("-WIZWALL-LAYOUT-", "2x2")
                
                # Build account mapping for window identification
                account_map = build_active_account_map(active_sessions, accounts)
                
                # Get all wizard windows
                windows = win_manager.get_wizard_windows(account_map)
                
                if not windows:
                    sg.popup("No Wizard101 windows found.\nStart some instances first!", title=APP_NAME)
                else:
                    # Arrange in selected grid layout
                    arranged = win_manager.arrange_grid(windows, layout=layout, padding=10)
                    
                    # Update window list display
                    window_info = "\n".join([
                        f"[{i+1}] {w.account_name} - Handle: {w.handle}"
                        for i, w in enumerate(windows)
                    ])
                    window["-WIZWALL-WINDOWS-"].update(window_info)
                    
                    window["-STATUS-"].update(f"✓ Arranged {arranged} windows in {layout} layout")
                    logger.info(f"Arranged {arranged} windows in {layout} layout")
            except Exception as e:
                logger.exception(f"Window arrangement failed: {e}")
                sg.popup_error(f"Failed to arrange windows:\n{str(e)}", title=APP_NAME)

        # Refresh Window List - Show current windows
        if event == "-REFRESH-WINDOWS-" and win_manager:
            try:
                account_map = build_active_account_map(active_sessions, accounts)
                windows = win_manager.get_wizard_windows(account_map)
                
                if windows:
                    window_info = "\n".join([
                        f"[{i+1}] {w.account_name} - Handle: {w.handle} - {w.title}"
                        for i, w in enumerate(windows)
                    ])
                    window["-WIZWALL-WINDOWS-"].update(window_info)
                    window["-STATUS-"].update(f"Found {len(windows)} active windows")
                else:
                    window["-WIZWALL-WINDOWS-"].update("No Wizard101 windows detected.")
                    window["-STATUS-"].update("No active windows found")
                    
            except Exception as e:
                logger.exception(f"Window refresh failed: {e}")
                sg.popup_error(f"Failed to refresh windows:\n{str(e)}", title=APP_NAME)
        
        # Save Current Layout
        if event == "-SAVE-LAYOUT-" and win_manager:
            try:
                account_map = build_active_account_map(active_sessions, accounts)
                windows = win_manager.get_wizard_windows(account_map)
                
                if not windows:
                    sg.popup("No windows to save layout for.", title=APP_NAME)
                else:
                    layout_data = win_manager.save_current_layout(windows)
                    # Store in config for persistence
                    config["saved_window_layout"] = layout_data
                    save_config(paths["config_file"], config)
                    window["-STATUS-"].update(f"✓ Saved layout for {len(layout_data)} windows")
                    sg.popup_ok(f"Saved positions for {len(layout_data)} windows", title="Layout Saved")
            except Exception as e:
                logger.exception(f"Save layout failed: {e}")
                sg.popup_error(f"Failed to save layout:\n{str(e)}", title=APP_NAME)
        
        # Restore Saved Layout
        if event == "-RESTORE-LAYOUT-" and win_manager:
            try:
                layout_data = config.get("saved_window_layout", {})
                if not layout_data:
                    sg.popup("No saved layout found.\nUse 'Save Current Layout' first.", title=APP_NAME)
                else:
                    restored = win_manager.restore_layout(layout_data)
                    window["-STATUS-"].update(f"✓ Restored {restored} windows")
                    sg.popup_ok(f"Restored {restored} windows to saved positions", title="Layout Restored")
            except Exception as e:
                logger.exception(f"Restore layout failed: {e}")
                sg.popup_error(f"Failed to restore layout:\n{str(e)}", title=APP_NAME)

        # Handle region-specific save and set as current
        for region_code in REGION_META:
            set_region_key = f"-SET-REGION-{region_code.upper()}-"
            reset_region_key = f"-RESET-{region_code.upper()}-"
            
            # Set region as current
            if event == set_region_key:
                # Save region-specific settings
                install_key = f"-INSTALL-{region_code.upper()}-"
                server_key = f"-SERVER-{region_code.upper()}-"
                port_key = f"-PORT-{region_code.upper()}-"
                
                config[f"region_install_{region_code}"] = values.get(install_key, "").strip()
                config[f"region_server_{region_code}"] = values.get(server_key, "").strip()
                try:
                    config[f"region_port_{region_code}"] = int(values.get(port_key, 12000))
                except ValueError:
                    config[f"region_port_{region_code}"] = 12000
                
                # Set as current region
                config["current_region"] = region_code
                save_config(paths["config_file"], config)
                

                window.close()
                window = build_window(config, accounts)
                region_name = REGION_META[region_code]["name"]
                window["-STATUS-"].update(f"✓ Switched to {region_name}")
            
            # Reset region to defaults
            if event == reset_region_key:
                region_info = REGION_META.get(region_code, {})
                config[f"region_install_{region_code}"] = ""
                config[f"region_server_{region_code}"] = region_info.get("server", "")
                config[f"region_port_{region_code}"] = 12000
                save_config(paths["config_file"], config)
                
                # Rebuild window to reflect defaults

                window.close()
                window = build_window(config, accounts)
                region_name = REGION_META[region_code]["name"]
                window["-STATUS-"].update(f"Reset {region_name} to defaults")

        # Open log folder
        if event == "Open log folder":
            os.startfile(str(paths["log_dir"]))

        # ================== LIVE LOG VIEWER HANDLERS ==================
        # Refresh logs - reload log file
        if event == "-LOG-REFRESH-" and log_viewer:
            try:
                latest_logs = log_viewer.get_latest_logs(num_lines=100)
                log_text = "\n".join(latest_logs)
                window["-LOG-OUTPUT-"].update(log_text)
                window["-STATUS-"].update("✓ Logs refreshed")
            except Exception as e:
                logger.warning(f"Log refresh failed: {e}")

        # Search logs
        if event == "-LOG-SEARCH-BTN-" and log_viewer:
            search_query = values.get("-LOG-SEARCH-", "")
            if search_query:
                try:
                    results = log_viewer.search_logs(search_query, case_sensitive=False)
                    log_text = "\n".join(results) if results else "No matches found"
                    window["-LOG-OUTPUT-"].update(log_text)
                    window["-STATUS-"].update(f"✓ Found {len(results)} matches")
                except Exception as e:
                    logger.warning(f"Log search failed: {e}")

        # Clear log search
        if event == "-LOG-CLEAR-BTN-" and log_viewer:
            window["-LOG-SEARCH-"].update("")
            try:
                latest_logs = log_viewer.get_latest_logs(num_lines=100)
                log_text = "\n".join(latest_logs)
                window["-LOG-OUTPUT-"].update(log_text)
                window["-STATUS-"].update("✓ Log view cleared")
            except Exception as e:
                logger.warning(f"Log clear failed: {e}")

        # Filter logs by level
        if event == "-LOG-LEVEL-" and log_viewer:
            selected_level = values.get("-LOG-LEVEL-", "All")
            try:
                if selected_level == "All":
                    logs = log_viewer.get_latest_logs(num_lines=100)
                else:
                    logs = log_viewer.filter_by_level(selected_level)
                log_text = "\n".join(logs)
                window["-LOG-OUTPUT-"].update(log_text)
                window["-STATUS-"].update(f"✓ Filtered to {selected_level}")
            except Exception as e:
                logger.warning(f"Log filter failed: {e}")

        # Export full log to file
        if event == "-LOG-EXPORT-" and log_viewer:
            try:
                all_logs = log_viewer.get_all_logs()
                export_file = paths["data_dir"] / f"launcher_export_{int(time.time())}.txt"
                with open(export_file, "w", encoding="utf-8") as f:
                    f.write("\n".join(all_logs))
                window["-STATUS-"].update(f"✓ Log exported to {export_file.name}")
                logger.info(f"Log exported to {export_file}")
            except Exception as e:
                logger.warning(f"Log export failed: {e}")
                sg.popup(f"Export failed: {e}", title=APP_NAME)

        # ================== THEME TOGGLE HANDLER ==================
        # Switch between Dark and Light themes
        if event == "-THEME-":
            new_theme = values.get("-THEME-", "WizDark")
            if new_theme != config.get("ui_theme", "WizDark"):
                config["ui_theme"] = new_theme
                save_config(paths["config_file"], config)
                
                # Apply theme and rebuild window
                apply_theme(config)

                window.close()
                window = build_window(config, accounts, log_file_path=str(log_file))
                window["-STATUS-"].update(f"✓ Theme changed to {new_theme}")
                logger.info(f"Theme changed to {new_theme}")

        # Mascot on/off: applied right away
        if event == "-MASCOT-ON-":
            config["mascot_enabled"] = bool(values.get("-MASCOT-ON-", True))
            save_config(paths["config_file"], config)
            window.close()
            window = build_window(config, accounts, log_file_path=str(log_file))
            window["-STATUS-"].update("Mascot " + ("shown" if config["mascot_enabled"] else "hidden"))

        # Stats: Refresh - rebuild window with updated stats
        if event == "Refresh":

            window.close()
            window = build_window(config, accounts, log_file_path=str(log_file))
            window["-STATUS-"].update("Stats refreshed")

        # Stats: Export - save playtime stats to file
        if event == "Export Stats":
            try:
                stats_list = tracker.get_accounts_stats_sorted(accounts, sort_by="playtime")
                export_file = paths["data_dir"] / "playtime_stats.txt"
                with open(export_file, "w", encoding="utf-8") as f:
                    f.write("=== Wizard101 Playtime Statistics ===\n\n")
                    for stat in stats_list:
                        f.write(f"Name: {stat['name']}\n")
                        f.write(f"Username: {stat['username']}\n")
                        f.write(f"Total Playtime: {stat['total_playtime']}\n")
                        f.write(f"Sessions: {stat['sessions']}\n")
                        f.write(f"Avg Session: {stat['avg_session']}\n")
                        f.write("-" * 40 + "\n\n")
                window["-STATUS-"].update(f"✓ Stats exported to {export_file.name}")
                logger.info(f"Stats exported to {export_file}")
            except Exception as exc:
                sg.popup(f"Export failed: {exc}", title=APP_NAME)
                logger.exception("Stats export failed")

        # Stats: Reset All - clear all playtime data
        if event == "Reset All Stats":
            if sg.popup_yes_no("Reset ALL playtime stats? This cannot be undone!", title=APP_NAME) == "Yes":
                for account in accounts:
                    account.total_playtime = 0.0
                    account.sessions_count = 0
                    account.last_session_start = 0.0
                save_accounts(paths["accounts_file"], master_password, accounts)

                window.close()
                window = build_window(config, accounts, log_file_path=str(log_file))
                window["-STATUS-"].update("✓ All stats cleared")
                logger.info("All playtime stats reset")

        # ================== KEYBOARD SHORTCUTS ==================
        # F1 - Quicklaunch
        if event == "F1":
            try:
                start_instance(config)
                window["-STATUS-"].update("Quicklaunch started (F1)")
            except Exception as exc:
                sg.popup(f"Launch failed: {exc}", title=APP_NAME)

        # F2 - Start Selected Instances
        if event == "F2":
            try:
                selected = values.get("-AUTO-ACCOUNTS-", [])
                region_accounts = get_region_accounts(accounts, config.get("current_region", "de"))
                selected_accounts = [
                    acc for acc in region_accounts if account_display(acc) in selected
                ]
                if not selected_accounts:
                    sg.popup("Select at least one account to start instances (F2)", title=APP_NAME)
                else:
                    count = len(selected_accounts)
                    for _ in range(count):
                        start_instance(config)
                    window["-STATUS-"].update(f"Started {count} instance(s) (F2)")
            except Exception as exc:
                sg.popup(f"Launch failed: {exc}", title=APP_NAME)

        # F3 - Start with Auto-Login (all selected accounts)
        if event == "F3":
            if not WIZWALKER_AVAILABLE:
                sg.popup("wizwalker not installed", title=APP_NAME)
            else:
                selected = values.get("-AUTO-ACCOUNTS-", [])
                region_accounts = get_region_accounts(accounts, config.get("current_region", "de"))
                selected_accounts = [
                    acc for acc in region_accounts if account_display(acc) in selected
                ]
                if not selected_accounts:
                    sg.popup("Select at least one account for auto-login (F3)", title=APP_NAME)
                else:
                    begin_auto_login(window, values, config, selected_accounts, "F3")

        # F4 - Minimize to tray (or toggle window visibility)
        if event == "F4":
            if window.is_hidden():
                window.un_hide()
                window["-STATUS-"].update("Window restored (F4)")
            else:
                window.hide()
                window["-STATUS-"].update("Minimized to background (F4)")

        # Ctrl+E - Export Stats shortcut
        if event == "Ctrl+E":
            try:
                stats_list = tracker.get_accounts_stats_sorted(accounts, sort_by="playtime")
                export_file = paths["data_dir"] / "playtime_stats.txt"
                with open(export_file, "w", encoding="utf-8") as f:
                    f.write("=== Wizard101 Playtime Statistics ===\n\n")
                    for stat in stats_list:
                        f.write(f"Name: {stat['name']}\n")
                        f.write(f"Username: {stat['username']}\n")
                        f.write(f"Total Playtime: {stat['total_playtime']}\n")
                        f.write(f"Sessions: {stat['sessions']}\n")
                        f.write(f"Avg Session: {stat['avg_session']}\n")
                        f.write("-" * 40 + "\n\n")
                window["-STATUS-"].update(f"✓ Stats exported (Ctrl+E)")
                logger.info(f"Stats exported via shortcut")
            except Exception as exc:
                logger.exception("Stats export failed")

        # Ctrl+S - Save Settings shortcut
        if event == "Ctrl+S":
            try:
                config["login_wait_seconds"] = float(values.get("-WAIT-", 5))
            except ValueError:
                config["login_wait_seconds"] = 5
            config["window_title_template"] = values.get("-TITLE-", "{name} ({username})")
            config["log_level"] = values.get("-LOGLEVEL-", "INFO")
            config["discord_rich_presence"] = values.get("-DISCORD-RPC-", True)
            config["discord_rpc_client_id"] = values.get("-DISCORD-RPC-ID-", "").strip()
            try:
                config["discord_rpc_update_interval"] = max(5, int(values.get("-DISCORD-RPC-INTERVAL-", 15)))
            except ValueError:
                config["discord_rpc_update_interval"] = 15
            
            # Save region visibility settings
            for region_code in REGION_META:
                toggle_key = f"-SHOW-{region_code.upper()}-"
                config[f"show_region_{region_code}"] = values.get(toggle_key, False)
            
            save_config(paths["config_file"], config)
            window["-STATUS-"].update("✓ Settings saved (Ctrl+S)")
            logger.info("Settings saved via shortcut")

    # Cleanup: End all active sessions before closing
    if active_sessions:
        logger.info(f"Ending {len(active_sessions)} active playtime sessions...")
        for handle, acc_username in list(active_sessions.items()):
            acc = get_account_by_username(accounts, acc_username)
            if acc:
                track_session_end(
                    tracker,
                    handle,
                    acc,
                    accounts,
                    reason="app_shutdown",
                )
            else:
                logger.warning(
                    "Skipping shutdown playtime finalize for unknown username=%s handle=%s",
                    acc_username,
                    handle,
                )
        # Save final playtime data
        save_accounts(paths["accounts_file"], master_password, accounts)
        logger.info("Playtime sessions saved")



    activity_24h.close()
    discord_presence.close()

    window.close()
    logger.info("Launcher closed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        import PySimpleGUI as sg
        sg.popup_error(f"FATAL ERROR:\n{str(e)}\n\nCheck console for details.", title="Launcher Fatal Error")
        raise SystemExit(1)














