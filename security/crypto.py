"""Encryption and account management module."""

import base64
import hmac
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import PySimpleGUI as sg
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from core.config import APP_NAME


@dataclass
class Account:
    """Account data structure."""

    name: str
    username: str
    password: str
    region: str = "de"  # Region code (de, us, fr, etc.)
    last_used: float = 0.0
    total_playtime: float = 0.0  # Sekunden
    last_session_start: float = 0.0  # Zeitstempel
    sessions_count: int = 0  # Anzahl der Spielsessions


def derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    """Derive encryption key from password using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt_accounts(password: str, accounts: List[Account]) -> dict:
    """Encrypt accounts with master password."""
    salt = os.urandom(16)
    iterations = 390000
    key = derive_key(password, salt, iterations)
    fernet = Fernet(key)

    payload = [account.__dict__ for account in accounts]
    token = fernet.encrypt(json.dumps(payload).encode("utf-8"))

    return {
        "salt": base64.b64encode(salt).decode("ascii"),
        "iterations": iterations,
        "data": token.decode("ascii"),
    }


def decrypt_accounts(password: str, encrypted: dict) -> List[Account]:
    """Decrypt accounts with master password."""
    salt = base64.b64decode(encrypted["salt"].encode("ascii"))
    iterations = int(encrypted.get("iterations", 390000))
    key = derive_key(password, salt, iterations)
    fernet = Fernet(key)

    payload = fernet.decrypt(encrypted["data"].encode("ascii"))
    accounts = json.loads(payload.decode("utf-8"))
    return [Account(**item) for item in accounts]


def load_accounts(accounts_file: Path, password: str) -> List[Account]:
    """Load encrypted accounts from file."""
    if not accounts_file.exists():
        return []

    with accounts_file.open("r", encoding="utf-8") as handle:
        encrypted = json.load(handle)
    return decrypt_accounts(password, encrypted)


def save_accounts(
    accounts_file: Path, password: str, accounts: List[Account]
) -> None:
    """Save accounts encrypted to file."""
    encrypted = encrypt_accounts(password, accounts)
    with accounts_file.open("w", encoding="utf-8") as handle:
        json.dump(encrypted, handle, indent=2, sort_keys=True)


def verify_master_password(entered: str, current: str) -> bool:
    """Constant-time check of an entered master password against the active one."""
    return hmac.compare_digest(entered.encode("utf-8"), current.encode("utf-8"))


def change_master_password(
    accounts_file: Path, accounts: List[Account], new_password: str
) -> None:
    """
    Re-encrypt all accounts with a new master password.

    The new file is written next to the old one, proven to decrypt with the new
    password, and only then swapped in atomically - so a crash or error can never
    leave you with a half-written (unreadable) accounts file. No backup of the
    old file is kept: it would stay decryptable with the old password.
    """
    encrypted = encrypt_accounts(new_password, accounts)
    tmp_file = accounts_file.with_name(accounts_file.name + ".tmp")
    try:
        with tmp_file.open("w", encoding="utf-8") as handle:
            json.dump(encrypted, handle, indent=2, sort_keys=True)
        load_accounts(tmp_file, new_password)  # raises if it doesn't round-trip
        os.replace(tmp_file, accounts_file)
    finally:
        if tmp_file.exists():
            tmp_file.unlink()


def change_master_password_dialog(current_master: str) -> Optional[str]:
    """
    Ask for the current master password plus a new one (twice).

    Returns the new master password, or None if cancelled. The current password
    must be entered correctly before anything can change.
    """
    layout = [
        [sg.Text("Current master password", size=(24, 1)),
         sg.Input(key="-OLD-", password_char="*", size=(30, 1))],
        [sg.Text("New master password", size=(24, 1)),
         sg.Input(key="-NEW-", password_char="*", size=(30, 1))],
        [sg.Text("Confirm new password", size=(24, 1)),
         sg.Input(key="-NEW2-", password_char="*", size=(30, 1))],
        [sg.Checkbox("Show passwords", key="-SHOW-", enable_events=True)],
        [sg.Text("", key="-ERR-", text_color="#FF5C7A", size=(56, 1))],
        [sg.Button("Change", bind_return_key=True), sg.Button("Cancel")],
    ]
    window = sg.Window("Change master password", layout, modal=True, finalize=True)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                return None
            if event == "-SHOW-":
                char = "" if values["-SHOW-"] else "*"
                for key in ("-OLD-", "-NEW-", "-NEW2-"):
                    window[key].update(password_char=char)
                continue
            if event != "Change":
                continue

            old, new, new2 = values["-OLD-"], values["-NEW-"], values["-NEW2-"]
            if not verify_master_password(old, current_master):
                window["-ERR-"].update("Current master password is wrong.")
                window["-OLD-"].update("")
            elif not new:
                window["-ERR-"].update("The new master password must not be empty.")
            elif verify_master_password(new, current_master):
                window["-ERR-"].update("The new password must differ from the current one.")
            elif new != new2:
                window["-ERR-"].update("The new passwords do not match.")
            else:
                return new
    finally:
        window.close()


def prompt_master_password(first_time: bool) -> Optional[str]:
    """Prompt user for master password (create on first run)."""
    if first_time:
        pw1 = sg.popup_get_text(
            "Create master password",
            title=APP_NAME,
            password_char="*",
        )
        if not pw1:
            return None
        pw2 = sg.popup_get_text(
            "Confirm master password",
            title=APP_NAME,
            password_char="*",
        )
        if pw1 != pw2:
            sg.popup("Passwords do not match", title=APP_NAME)
            return None
        return pw1

    pw = sg.popup_get_text(
        "Enter master password",
        title=APP_NAME,
        password_char="*",
    )
    return pw or None


def load_master_password(accounts_file: Path) -> Optional[str]:
    """Prompt and validate master password."""
    first_time = not accounts_file.exists()
    while True:
        password = prompt_master_password(first_time)
        if password is None:
            return None

        if first_time:
            return password

        try:
            load_accounts(accounts_file, password)
            return password
        except InvalidToken:
            sg.popup("Master password invalid", title=APP_NAME)
        except Exception as exc:
            sg.popup(f"Failed to unlock: {exc}", title=APP_NAME)


def account_display(account: Account) -> str:
    """Format account for display in UI."""
    return f"{account.name} ({account.username})"


def add_edit_account(account: Optional[Account] = None, current_region: str = "de") -> Optional[Account]:
    """Dialog to add or edit an account."""
    from core.config import REGION_META
    
    # Build region options
    region_options = []
    for region_code, region_info in REGION_META.items():
        region_display = f"{region_code.upper()} - {region_info['name']}"
        region_options.append(region_display)
    
    # Get default region
    if account:
        default_region = f"{account.region.upper()} - {REGION_META.get(account.region, {}).get('name', account.region.upper())}"
    else:
        default_region = f"{current_region.upper()} - {REGION_META.get(current_region, {}).get('name', current_region.upper())}"
    
    layout = [
        [sg.Text("Name"), sg.Input(account.name if account else "", key="-NAME-")],
        [
            sg.Text("Username"),
            sg.Input(account.username if account else "", key="-USER-"),
        ],
        [
            sg.Text("Password"),
            sg.Input(
                account.password if account else "",
                key="-PASS-",
                password_char="*",
            ),
        ],
        [
            sg.Checkbox(
                "Show password",
                default=False,
                key="-SHOW-PASS-",
                enable_events=True,
            ),
        ],
        [
            sg.Text("Region"),
            sg.Combo(
                region_options,
                default_value=default_region,
                key="-REGION-",
                readonly=True,
                size=(30, 1)
            )
        ],
        [sg.Button("Save"), sg.Button("Cancel")],
    ]

    window = sg.Window("Account", layout, modal=True)
    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, "Cancel"):
            window.close()
            return None
        if event == "-SHOW-PASS-":
            # Toggle between masked and plain text
            window["-PASS-"].update(password_char="" if values["-SHOW-PASS-"] else "*")
            continue
        if event == "Save":
            name = values["-NAME-"].strip()
            username = values["-USER-"].strip()
            password = values["-PASS-"].strip()
            region_display = values["-REGION-"]
            
            # Extract region code from display (e.g., "DE - Deutschland" -> "de")
            region_code = region_display.split(" - ")[0].lower() if region_display else current_region
            
            if not name or not username or not password:
                sg.popup("Please fill all fields", title=APP_NAME)
                continue
            window.close()
            
            # Preserve existing account data if editing
            if account:
                return Account(
                    name=name,
                    username=username,
                    password=password,
                    region=region_code,
                    last_used=account.last_used,
                    total_playtime=account.total_playtime,
                    last_session_start=account.last_session_start,
                    sessions_count=account.sessions_count
                )
            else:
                return Account(name=name, username=username, password=password, region=region_code)

