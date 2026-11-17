"""Encrypted export / import of the launcher's settings and accounts.

A backup is ONE file (.qlbackup). Everything inside - settings and accounts - is
encrypted with the master password using the same scheme as accounts.enc.json
(PBKDF2-SHA256 + Fernet), so the file is useless without that password.

Exporting asks for the *current* master password. Importing asks for the password
the backup was made with, then stores the accounts under the *local* master
password. A backup file is untrusted input (anyone can make one with any
password), so on import only harmless preference keys are applied - never the
update source, login servers, launch arguments, install paths or webhooks.
"""

import base64
import json
import logging
import os
import time
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import PySimpleGUI as sg
from cryptography.fernet import Fernet, InvalidToken

from core.config import APP_NAME, APP_VERSION, DEFAULT_CONFIG, normalize_config
from security.crypto import Account, derive_key, verify_master_password

BACKUP_FORMAT = "QuickLauncherBackup"
BACKUP_VERSION = 1
BACKUP_EXTENSION = ".qlbackup"
MAX_BACKUP_BYTES = 5 * 1024 * 1024          # a real backup is a few KB
KDF_ITERATIONS = 390000                      # same as the accounts file
_MIN_ITER, _MAX_ITER = 100_000, 1_000_000    # reject absurd values from a hostile file

# Settings that are never imported (machine specific, or could redirect credentials,
# updates or notifications if a hostile backup carried them).
_SKIP_KEYS = {
    "github_repo",          # update source: would let a backup redirect the Update button
    "extra_args",           # extra command-line arguments for the game
    "app_icon_path",
    "discord_webhook_url",  # notifications could be sent to someone else
    "install_path", "default_install", "default_install_us",
    "last_update_check",
}
_SKIP_PREFIXES = ("region_install_", "region_server_", "region_port_")

_ACCOUNT_FIELDS = {f.name: f for f in fields(Account)}


class BackupError(Exception):
    """Something is wrong with the backup file or password (message is user-presentable)."""


# ----------------------------------------------------------------------------
# File format
# ----------------------------------------------------------------------------

def _type_matches(default, value) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


def importable_settings(config: dict) -> Dict:
    """The subset of a backup's settings that may be applied (known keys, right types, not skipped)."""
    result = {}
    for key, value in config.items():
        if key in _SKIP_KEYS or key.startswith(_SKIP_PREFIXES):
            continue
        if key in DEFAULT_CONFIG and _type_matches(DEFAULT_CONFIG[key], value):
            result[key] = value
    return result


def make_backup(config: dict, accounts: List[Account], password: str) -> dict:
    """Encrypt settings + accounts into the on-disk envelope."""
    payload = {
        "format": BACKUP_FORMAT,
        "backup_version": BACKUP_VERSION,
        "app_version": APP_VERSION,
        "exported_at": time.time(),
        "config": config,
        "accounts": [account.__dict__ for account in accounts],
    }
    salt = os.urandom(16)
    key = derive_key(password, salt, KDF_ITERATIONS)
    token = Fernet(key).encrypt(json.dumps(payload).encode("utf-8"))
    return {
        "format": BACKUP_FORMAT,
        "backup_version": BACKUP_VERSION,
        "salt": base64.b64encode(salt).decode("ascii"),
        "iterations": KDF_ITERATIONS,
        "data": token.decode("ascii"),
    }


def write_backup_file(path: Path, envelope: dict) -> None:
    """Write atomically so a failure never leaves a half-written backup behind."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_backup_file(path: Path) -> dict:
    """Load and sanity-check the envelope (does not decrypt)."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_BACKUP_BYTES:
            raise BackupError("This file is too large to be a QuickLauncher backup.")
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("This is not a QuickLauncher backup file.") from exc
    if (not isinstance(envelope, dict) or envelope.get("format") != BACKUP_FORMAT
            or not all(k in envelope for k in ("salt", "iterations", "data"))):
        raise BackupError("This is not a QuickLauncher backup file.")
    if envelope.get("backup_version", 0) > BACKUP_VERSION:
        raise BackupError("This backup was made by a newer QuickLauncher. Please update first.")
    return envelope


def decrypt_backup(envelope: dict, password: str) -> dict:
    """Decrypt + validate. Raises BackupError (wrong password / damaged file)."""
    try:
        iterations = int(envelope["iterations"])
        salt = base64.b64decode(envelope["salt"].encode("ascii"))
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise BackupError("The backup file is damaged.") from exc
    if not _MIN_ITER <= iterations <= _MAX_ITER:
        raise BackupError("The backup file is damaged.")
    try:
        raw = Fernet(derive_key(password, salt, iterations)).decrypt(envelope["data"].encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except InvalidToken as exc:
        raise BackupError("Wrong password, or the backup file is damaged.") from exc
    except (ValueError, UnicodeError) as exc:
        raise BackupError("The backup file is damaged.") from exc
    return _validate_payload(payload)


def _validate_payload(payload) -> dict:
    if (not isinstance(payload, dict) or payload.get("format") != BACKUP_FORMAT
            or not isinstance(payload.get("config"), dict)
            or not isinstance(payload.get("accounts"), list)):
        raise BackupError("The backup file is damaged.")
    accounts: List[Account] = []
    for item in payload["accounts"]:
        if not isinstance(item, dict):
            raise BackupError("The backup file is damaged.")
        values = {}
        for name, spec in _ACCOUNT_FIELDS.items():
            if name in item:
                values[name] = item[name]
        for required in ("name", "username", "password"):
            if not isinstance(values.get(required), str) or not values[required]:
                raise BackupError("The backup contains an invalid account.")
        if not isinstance(values.get("region", "de"), str):
            raise BackupError("The backup contains an invalid account.")
        for numeric in ("last_used", "total_playtime", "last_session_start"):
            if numeric in values and (isinstance(values[numeric], bool) or not isinstance(values[numeric], (int, float))):
                values[numeric] = 0.0
        if "sessions_count" in values and (isinstance(values["sessions_count"], bool)
                                           or not isinstance(values["sessions_count"], int)):
            values["sessions_count"] = 0
        accounts.append(Account(**values))
    return {"config": payload["config"], "accounts": accounts,
            "app_version": str(payload.get("app_version", "?")),
            "exported_at": payload.get("exported_at")}


# ----------------------------------------------------------------------------
# Applying an import (pure functions - easy to test)
# ----------------------------------------------------------------------------

def _account_key(account: Account):
    return account.username.strip().lower(), account.region


def merge_accounts(current: List[Account], imported: List[Account], replace: bool):
    """Returns (new_list, added_count, skipped_count)."""
    if replace:
        return list(imported), len(imported), 0
    known = {_account_key(a) for a in current}
    merged, added, skipped = list(current), 0, 0
    for account in imported:
        if _account_key(account) in known:
            skipped += 1
        else:
            merged.append(account)
            known.add(_account_key(account))
            added += 1
    return merged, added, skipped


def apply_import(current_config: dict, current_accounts: List[Account], data: dict, *,
                 import_accounts: bool, import_settings: bool, replace_accounts: bool) -> dict:
    """Combine a decrypted backup with the current state. Does not touch any file."""
    config, accounts, added, skipped = dict(current_config), list(current_accounts), 0, 0
    if import_settings:
        config.update(importable_settings(data["config"]))
        config = normalize_config(config)
    if import_accounts:
        accounts, added, skipped = merge_accounts(current_accounts, data["accounts"], replace_accounts)
    return {"config": config, "accounts": accounts, "added": added, "skipped": skipped,
            "settings": import_settings, "replaced": import_accounts and replace_accounts}


# ----------------------------------------------------------------------------
# Dialogs (thin; the logic above is what gets tested)
# ----------------------------------------------------------------------------

def password_dialog(title: str, prompt: str,
                    check: Optional[Callable[[str], Optional[str]]] = None) -> Optional[str]:
    """
    Ask for a password. `check(pw)` may return an error text to keep the dialog open.
    Returns the password, or None if cancelled.
    """
    layout = [
        [sg.Text(prompt, size=(52, 3))],
        [sg.Input(key="-PW-", password_char="*", size=(40, 1))],
        [sg.Checkbox("Show password", key="-SHOW-", enable_events=True)],
        [sg.Text("", key="-ERR-", text_color="#FF5C7A", size=(52, 1))],
        [sg.Button("OK", bind_return_key=True), sg.Button("Cancel")],
    ]
    window = sg.Window(title, layout, modal=True, finalize=True)
    try:
        window["-PW-"].set_focus()
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                return None
            if event == "-SHOW-":
                window["-PW-"].update(password_char="" if values["-SHOW-"] else "*")
                continue
            if event != "OK":
                continue
            password = values["-PW-"]
            if not password:
                window["-ERR-"].update("Please enter the password.")
                continue
            error = check(password) if check else None
            if error:
                window["-ERR-"].update(error)
                window["-PW-"].update("")
                continue
            return password
    finally:
        window.close()


def import_options_dialog(summary: str, account_count: int) -> Optional[dict]:
    """What to import. Returns {"accounts", "settings", "replace"} or None."""
    layout = [
        [sg.Text(summary, size=(60, 3))],
        [sg.Checkbox(f"Import accounts ({account_count})", default=True, key="-ACC-", enable_events=True)],
        [sg.Radio("Add missing accounts (keep my existing ones)", "MODE", default=True, key="-MERGE-", pad=((30, 0), 2))],
        [sg.Radio("Replace ALL my accounts with the backup", "MODE", key="-REPLACE-", pad=((30, 0), 2))],
        [sg.Checkbox("Import settings (theme, regions, log level, ...)", default=True, key="-SET-")],
        [sg.Text("Install paths, login servers, update source and webhooks are never imported.",
                 size=(60, 1), text_color="#9A9CA6")],
        [sg.Text("", key="-ERR-", text_color="#FF5C7A", size=(60, 1))],
        [sg.Button("Import"), sg.Button("Cancel")],
    ]
    window = sg.Window("Import backup", layout, modal=True, finalize=True)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                return None
            if event == "-ACC-":
                window["-MERGE-"].update(disabled=not values["-ACC-"])
                window["-REPLACE-"].update(disabled=not values["-ACC-"])
                continue
            if event == "Import":
                if not values["-ACC-"] and not values["-SET-"]:
                    window["-ERR-"].update("Choose at least one thing to import.")
                    continue
                return {"accounts": values["-ACC-"], "settings": values["-SET-"],
                        "replace": bool(values["-REPLACE-"])}
    finally:
        window.close()


def _documents_folder() -> str:
    docs = Path.home() / "Documents"
    return str(docs if docs.is_dir() else Path.home())


def ask_save_path() -> Optional[str]:
    name = f"QuickLauncher-backup-{datetime.now():%Y-%m-%d}{BACKUP_EXTENSION}"
    return sg.popup_get_file(
        "Save backup", save_as=True, no_window=True, default_path=name,
        default_extension=BACKUP_EXTENSION, initial_folder=_documents_folder(),
        file_types=(("QuickLauncher backup", f"*{BACKUP_EXTENSION}"),),
    ) or None


def ask_open_path() -> Optional[str]:
    return sg.popup_get_file(
        "Choose a QuickLauncher backup", no_window=True, initial_folder=_documents_folder(),
        file_types=(("QuickLauncher backup", f"*{BACKUP_EXTENSION}"), ("All files", "*.*")),
    ) or None


# ----------------------------------------------------------------------------
# The two flows used by the Settings buttons
# ----------------------------------------------------------------------------

def run_export(config: dict, accounts: List[Account], master_password: str) -> Optional[str]:
    """
    Export flow. Needs the current master password. Returns a status text,
    or None if the user cancelled. Raises BackupError on failure.
    """
    def check(entered: str) -> Optional[str]:
        return None if verify_master_password(entered, master_password) else "Wrong master password."

    password = password_dialog(
        "Export backup",
        "Enter your master password to export your settings and accounts.\n"
        "The backup file is encrypted with it.", check)
    if password is None:
        return None
    path = ask_save_path()
    if not path:
        return None
    path = Path(path)
    if path.suffix.lower() != BACKUP_EXTENSION:
        path = path.with_name(path.name + BACKUP_EXTENSION)
    try:
        write_backup_file(path, make_backup(config, accounts, password))
    except OSError as exc:
        raise BackupError(f"Could not write the backup: {exc}") from exc
    logging.getLogger("backup").info("Backup exported (%s accounts)", len(accounts))
    return f"Backup saved: {path.name} ({len(accounts)} accounts)"


def run_import(config: dict, accounts: List[Account]) -> Optional[dict]:
    """
    Import flow. Returns {"config", "accounts", "message"} to apply, or None if cancelled.
    Raises BackupError for an unusable file.
    """
    path = ask_open_path()
    if not path:
        return None
    envelope = read_backup_file(Path(path))

    data: Dict = {}

    def check(entered: str) -> Optional[str]:
        try:
            data.update(decrypt_backup(envelope, entered))
            return None
        except BackupError as exc:
            return str(exc)

    password = password_dialog(
        "Import backup",
        "Enter the password this backup was made with\n(your master password at export time).", check)
    if password is None:
        return None

    exported = ""
    if data.get("exported_at"):
        exported = f", exported {datetime.fromtimestamp(float(data['exported_at'])):%Y-%m-%d %H:%M}"
    summary = (f"Backup from QuickLauncher v{data['app_version']}{exported}\n"
               f"{len(data['accounts'])} account(s) and your settings.")
    options = import_options_dialog(summary, len(data["accounts"]))
    if options is None:
        return None
    if options["accounts"] and options["replace"]:
        answer = sg.popup_yes_no(
            f"This REPLACES all {len(accounts)} of your current accounts with the "
            f"{len(data['accounts'])} from the backup.\n\nContinue?", title=APP_NAME)
        if answer != "Yes":
            return None

    result = apply_import(config, accounts, data, import_accounts=options["accounts"],
                          import_settings=options["settings"], replace_accounts=options["replace"])
    parts = []
    if options["accounts"]:
        parts.append(f"{result['added']} account(s) imported"
                     + ("" if result["replaced"] else f", {result['skipped']} already existed"))
    if options["settings"]:
        parts.append("settings imported")
    result["message"] = "Backup imported: " + ", ".join(parts)
    logging.getLogger("backup").info(result["message"])
    return result
