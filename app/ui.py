"""UI components and theme management.

Visual language: dark "dashboard" panels. Every group of controls lives in a
bordered card with a coloured accent bar, tabs are flat with a cyan selected
state, and buttons are flat colour blocks.

All element keys and the labels of key-less buttons (their label *is* the
event name) are unchanged, so app/main.py is unaffected by the restyle.
"""

import base64
import functools
import logging
import struct
import sys
import zlib
from pathlib import Path
from typing import Dict, List, Optional

import PySimpleGUI as sg

from core.config import APP_NAME, APP_VERSION, PUBLISHERS, REGION_META
from security.crypto import Account, account_display
from services.playtime_tracker import PlaytimeTracker
from services.git_sync import GitSync
from services.log_viewer import LogViewer


# ----------------------------------------------------------------------------
# Palettes
# ----------------------------------------------------------------------------

FONT = "Segoe UI"          # clean sans for the dashboard look
MONO = "Consolas"          # logs / console boxes

_PALETTES: Dict[str, Dict[str, str]] = {
    # Dark-grey "ops dashboard" (the colourful accents / gradient buttons stay)
    "WizDark": {
        "bg": "#111214",        # window
        "panel": "#1A1B1E",     # sidebar / content panels
        "card": "#25262B",      # card body
        "card_hi": "#34363D",   # selected nav item, chips, ghost buttons
        "border": "#3B3D45",
        "input": "#16171A",
        "text": "#EDEDF0",
        "muted": "#9A9CA6",
        "accent": "#3FB6FF",    # cyan-blue
        "gold": "#FFB84D",
        "green": "#3DDC97",
        "red": "#FF5C7A",
        "purple": "#9B7BFF",
        "blue": "#5B8CFF",
        "on_accent": "#06122B",  # text colour on a flat coloured button
        "hover_to": "#FFFFFF",
    },
    "WizLight": {
        "bg": "#E6ECFA",
        "panel": "#F2F5FD",
        "card": "#FFFFFF",
        "card_hi": "#DCE6FB",
        "border": "#B9C9EC",
        "input": "#F4F7FE",
        "text": "#0F1A3A",
        "muted": "#5C6B8F",
        "accent": "#1473E6",
        "gold": "#C77700",
        "green": "#13935B",
        "red": "#D13355",
        "purple": "#6E4BD8",
        "blue": "#3F63D6",
        "on_accent": "#FFFFFF",
        "hover_to": "#000000",
    },
}

# Active palette (swapped by apply_theme)
P: Dict[str, str] = dict(_PALETTES["WizDark"])


def _mix(c1: str, c2: str, t: float) -> str:
    """Blend two #RRGGBB colours (t=0 -> c1, t=1 -> c2)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


# ----------------------------------------------------------------------------
# Theme
# ----------------------------------------------------------------------------

def apply_theme(config: dict) -> None:
    """
    Apply the launcher theme (WizDark / WizLight) and global look & feel.

    Args:
        config: Configuration dictionary with 'ui_theme' key
    """
    selected = config.get("ui_theme", "WizDark")
    if selected not in _PALETTES:
        selected = "WizDark"
    P.clear()
    P.update(_PALETTES[selected])

    for name, pal in _PALETTES.items():
        sg.theme_add_new(
            name,
            {
                "BACKGROUND": pal["bg"],
                "TEXT": pal["text"],
                "INPUT": pal["input"],
                "TEXT_INPUT": pal["text"],
                "SCROLL": pal["card_hi"],
                "BUTTON": (pal["on_accent"], pal["accent"]),
                "PROGRESS": (pal["accent"], pal["input"]),
                "BORDER": 0,
                "SLIDER_DEPTH": 0,
                "PROGRESS_DEPTH": 0,
            },
        )
    sg.theme(selected)

    sg.set_options(
        font=(FONT, 10),
        element_padding=(6, 4),
        margins=(0, 0),
        border_width=0,
        button_element_size=(12, 1),
        scrollbar_color=P["card_hi"],
        sbar_trough_color=P["panel"],
        sbar_background_color=P["card_hi"],
        sbar_arrow_color=P["muted"],
        sbar_frame_color=P["panel"],
        sbar_relief=sg.RELIEF_FLAT,
        sbar_width=10,
        sbar_arrow_width=10,
    )


# ----------------------------------------------------------------------------
# Small styled-element helpers
# ----------------------------------------------------------------------------

def _t(text: str, *, bg: Optional[str] = None, color: Optional[str] = None,
       size: int = 10, bold: bool = False, **kw) -> sg.Text:
    """Text on a card (default) or any other background."""
    return sg.Text(
        text,
        background_color=bg or P["card"],
        text_color=color or P["text"],
        font=(FONT, size, "bold" if bold else "normal"),
        **kw,
    )


def _muted(text: str, **kw) -> sg.Text:
    return _t(text, color=P["muted"], size=9, **kw)


@functools.lru_cache(maxsize=256)
def _gradient_png(width: int, height: int, c1: str, c2: str, radius: int = 9) -> str:
    """Rounded horizontal-gradient pill as a base64 PNG (pure Python, no Pillow)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    rows = []
    for y in range(height):
        row = bytearray([0])  # PNG filter type 0
        cy = radius if y < radius else (height - 1 - radius if y > height - 1 - radius else y)
        for x in range(width):
            t = (x / max(width - 1, 1)) * 0.85 + (y / max(height - 1, 1)) * 0.15
            cx = radius if x < radius else (width - 1 - radius if x > width - 1 - radius else x)
            dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            alpha = max(0.0, min(1.0, radius - dist + 0.5))
            row += bytes((round(a[0] + (b[0] - a[0]) * t),
                          round(a[1] + (b[1] - a[1]) * t),
                          round(a[2] + (b[2] - a[2]) * t),
                          round(alpha * 255)))
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
           + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


def _btn(text: str, key: Optional[str] = None, kind: str = "ghost", **kw) -> sg.Button:
    """
    Dashboard button. kind: primary | success | warn | danger -> rounded gradient pill;
    ghost -> flat raised button. `size` is (characters, lines) as before.
    """
    chars, lines = kw.pop("size", None) or (max(len(text) + 4, 10), 1)
    kw.setdefault("pad", (6, 6))
    gradients = {
        "primary": (P["accent"], P["purple"]),
        "success": (P["green"], P["accent"]),
        "warn": (P["gold"], "#FF7A59"),
        "danger": (P["red"], "#C23A8A"),
    }
    if kind in gradients:
        width, height = int(chars * 7.4) + 28, lines * 22 + 14
        c1, c2 = gradients[kind]
        kwargs = dict(
            image_data=_gradient_png(width, height, c1, c2),
            button_color=("#FFFFFF", kw.pop("bg", None) or P["card"]),
            border_width=0,
            font=(FONT, 10, "bold"),
            **kw,
        )
    else:
        fg, bg = P["text"], P["card_hi"]
        kwargs = dict(
            size=(chars, lines),
            button_color=(fg, bg),
            mouseover_colors=(fg, _mix(bg, P["hover_to"], 0.18)),
            border_width=0,
            font=(FONT, 10, "bold"),
            **kw,
        )
    if key is not None:
        kwargs["key"] = key
    return sg.Button(text, **kwargs)


def _inp(default: str, key: str, **kw) -> sg.Input:
    return sg.Input(
        default,
        key=key,
        background_color=P["input"],
        text_color=P["text"],
        border_width=1,
        font=(FONT, 10),
        **kw,
    )


def _combo(values, default, key: str, **kw) -> sg.Combo:
    kw.setdefault("readonly", True)
    return sg.Combo(
        values,
        default_value=default,
        key=key,
        background_color=P["input"],
        text_color=P["text"],
        button_background_color=P["card_hi"],
        button_arrow_color=P["accent"],
        font=(FONT, 10),
        **kw,
    )


def _chk(text: str, default: bool, key: str, **kw) -> sg.Checkbox:
    return sg.Checkbox(
        text,
        default=default,
        key=key,
        background_color=P["card"],
        text_color=P["text"],
        checkbox_color=P["input"],
        font=(FONT, 10),
        **kw,
    )


def _listbox(values, key: str, size, **kw) -> sg.Listbox:
    return sg.Listbox(
        values=values,
        key=key,
        size=size,
        background_color=P["input"],
        text_color=P["text"],
        highlight_background_color=P["accent"],
        highlight_text_color=P["on_accent"],
        font=(FONT, 10),
        expand_x=True,
        **kw,
    )


def _log_box(size, key: str, **kw) -> sg.Multiline:
    return sg.Multiline(
        size=size,
        key=key,
        disabled=True,
        background_color=P["input"],
        text_color=P["text"],
        border_width=0,
        font=(MONO, 9),
        **kw,
    )


def _card(title: str, rows: List[List], accent: Optional[str] = None, pad=(0, 6),
          expand_y: bool = False) -> sg.Column:
    """
    Bordered card: coloured title, thin accent bar, then the given rows.
    The 1px border is the outer column's background showing around the body.
    """
    accent = accent or P["accent"]
    body = [
        [_t(title.upper(), color=accent, size=10, bold=True, pad=((8, 6), (6, 2)))],
        [sg.Text("", background_color=accent, font=(FONT, 1), expand_x=True,
                 pad=((8, 8), (0, 6)))],
    ] + rows
    inner = sg.Column(body, background_color=P["card"], pad=(1, 1),
                      expand_x=True, expand_y=expand_y)
    return sg.Column([[inner]], background_color=P["border"], pad=pad,
                     expand_x=True, expand_y=expand_y)


def _kpi(title: str, value: str, caption: str, glyph: str, color: str) -> sg.Column:
    """Dashboard stat tile: small caps title, big value, caption, glyph on the right."""
    card = P["card"]
    body = [
        [_t(title.upper(), color=P["muted"], size=8, bold=True, pad=((12, 6), (10, 0)))],
        [_t(value, size=20, bold=True, pad=((12, 6), (0, 0))),
         sg.Push(background_color=card),
         _t(glyph, color=color, size=18, pad=((6, 14), (0, 0)))],
        [_t(caption, color=P["muted"], size=8, pad=((12, 6), (0, 10)))],
    ]
    inner = sg.Column(body, background_color=card, pad=(1, 1), expand_x=True)
    return sg.Column([[inner]], background_color=P["border"], pad=(4, 6), expand_x=True)


def _page(rows: List[List]) -> List[List]:
    """Wrap a tab's content so it gets the panel background and padding."""
    return [[sg.Column(rows, background_color=P["panel"], pad=(10, 8),
                       expand_x=True, expand_y=True)]]


def _tab(title: str, layout: List[List], key: str) -> sg.Tab:
    return sg.Tab(f"  {title}  ", layout, key=key, background_color=P["panel"])


def _tabgroup(tabs: List[sg.Tab], key: Optional[str] = None, **kw) -> sg.TabGroup:
    kwargs = dict(
        tab_background_color=P["bg"],
        selected_background_color=P["panel"],
        title_color=P["muted"],
        selected_title_color=P["accent"],
        background_color=P["bg"],
        border_width=0,
        tab_border_width=1,
        font=(FONT, 10, "bold"),
        expand_x=True,
        expand_y=True,
        pad=(0, 0),
    )
    kwargs.update(kw)
    if key is not None:
        kwargs["key"] = key
    return sg.TabGroup([tabs], **kwargs)


# ----------------------------------------------------------------------------
# Icon
# ----------------------------------------------------------------------------

def resolve_icon_path(config: dict) -> Optional[str]:
    """
    Resolve an icon path for the main window.
    Supports .ico directly, or .png with optional Pillow conversion.
    """
    icon_path = str(config.get("app_icon_path", "")).strip()
    if not icon_path:
        return None

    path = Path(icon_path)
    if not path.is_absolute():
        # Look next to the installed exe / in the PyInstaller bundle / project root,
        # never depend on the current working directory.
        candidates = []
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).resolve().parent / path)
            candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / path)
        candidates.append(Path(__file__).resolve().parent.parent / path)
        candidates.append(Path.cwd() / path)
        path = next((c for c in candidates if c.exists()), candidates[0])

    if not path.exists():
        logging.getLogger("ui").warning("Icon path not found: %s", path)
        return None

    if path.suffix.lower() == ".ico":
        return str(path)

    if path.suffix.lower() == ".png":
        ico_path = path.with_suffix(".ico")
        if ico_path.exists():
            return str(ico_path)
        try:
            from PIL import Image

            with Image.open(path) as img:
                img.save(ico_path)
            return str(ico_path)
        except Exception as exc:
            logging.getLogger("ui").warning(
                "Failed to convert PNG icon to ICO: %s", exc
            )
            return str(path)

    logging.getLogger("ui").warning("Unsupported icon format: %s", path)
    return None


# ----------------------------------------------------------------------------
# Tabs
# ----------------------------------------------------------------------------

def build_launch_tab(config: dict, account_list: List[str]) -> List:
    """Build Launch tab layout (window management lives in the Extensions tab)."""
    current_region = config.get("current_region", "de")
    region_name = REGION_META.get(current_region, {}).get("name", current_region.upper())

    available_regions = []
    for region_code in REGION_META:
        if config.get(f"show_region_{region_code}", False):
            region_display = f"{region_code.upper()} - {REGION_META[region_code]['name']}"
            available_regions.append((region_display, region_code))

    default_region = f"{current_region.upper()} - {region_name}"

    region_card = _card("Region", [
        [_t("Current Region", color=P["muted"], size=9),
         _t(region_name.upper(), color=P["accent"], size=14, bold=True)],
        [
            _t("Switch Region"),
            _combo([r[0] for r in available_regions], default_region,
                   "-REGION-SELECT-", enable_events=True, size=(35, 1)),
            _btn("Apply Region", "-APPLY-REGION-", "primary"),
        ],
    ], accent=P["accent"])

    launch_card = _card("Launch", [
        [
            _btn("Quicklaunch (1 Instance)", "Quicklaunch", "primary", size=(26, 2)),
            _btn("Start Selected Instances", "Start Instances", "success", size=(26, 2)),
            _btn("Start Selected + Auto Login", "Start + Auto Login", "warn", size=(30, 2)),
        ],
        [_muted("F1 Quicklaunch   F2 Multi-Instance   F3 Auto-Login",
                pad=((8, 6), (0, 8)))],
    ], accent=P["gold"])

    accounts_card = _card("Accounts to launch", [
        [
            _t("Ctrl+Click to select multiple", color=P["muted"], size=9),
            sg.Push(background_color=P["card"]),
            _t("0 selected", key="-SELECTED-COUNT-", color=P["green"], size=10, bold=True),
        ],
        [_listbox(account_list, "-AUTO-ACCOUNTS-", (55, 6),
                  select_mode=sg.LISTBOX_SELECT_MODE_MULTIPLE,
                  enable_events=True, pad=(8, (2, 10)))],
    ], accent=P["green"])

    options_card = _card("Window options", [
        [
            _chk("Bring window to front", config.get("foreground_on_login", True), "-FOREGROUND-"),
            _chk("Set window title", config.get("set_window_title", True), "-SETTITLE-"),
        ],
    ], accent=P["purple"])

    enabled_regions = sum(bool(config.get(f"show_region_{c}", False)) for c in REGION_META)
    kpis = [
        _kpi("Region", region_name.upper(), "Active server region", "\u25ce", P["accent"]),
        _kpi("Accounts", str(len(account_list)), "Saved for this region", "\u2630", P["green"]),
        _kpi("Regions enabled", str(enabled_regions), f"of {len(REGION_META)} available", "\u2726", P["purple"]),
        _kpi("Log level", str(config.get("log_level", "INFO")), "Launcher logging", "\u2261", P["gold"]),
    ]
    return _page([kpis, [region_card], [launch_card], [accounts_card], [options_card]])


def build_region_tab(config: dict, region_code: str) -> List:
    """Build per-region settings tab."""
    region_info = REGION_META.get(region_code, {})
    region_name = region_info.get("name", region_code.upper())
    code = region_code.upper()

    settings_card = _card(f"Settings for {region_name}", [
        [
            _t("Install path", size=10),
            _inp(config.get(f"region_install_{region_code}", ""), f"-INSTALL-{code}-", size=(40, 1)),
            sg.FolderBrowse(
                key=f"-BROWSE-{code}-",
                button_color=(P["text"], P["card_hi"]),
                font=(FONT, 10, "bold"),
                size=(10, 1),
            ),
        ],
        [
            _t("Login Server", size=10),
            _inp(config.get(f"region_server_{region_code}", region_info.get("server", "")),
                 f"-SERVER-{code}-", size=(40, 1)),
        ],
        [
            _t("Port", size=10),
            _inp(str(config.get(f"region_port_{region_code}", 12000)), f"-PORT-{code}-", size=(10, 1)),
        ],
        [
            _btn(f"Set as Current ({region_name})", f"-SET-REGION-{code}-", "primary"),
            _btn(f"Reset {region_name} to Default", f"-RESET-{code}-", "danger"),
        ],
    ], accent=P["blue"])

    return _page([[settings_card]])


def build_accounts_tab(account_list: List[str]) -> List:
    """Build Accounts tab layout."""
    card = _card("Accounts", [
        [_listbox(account_list, "-ACCOUNTS-", (55, 12), enable_events=True,
                  pad=(8, (2, 6)))],
        [_btn("Add", kind="success"), _btn("Edit", kind="warn"), _btn("Delete", kind="danger")],
    ], accent=P["green"])
    return _page([[card]])


def build_settings_tab(config: dict) -> List:
    """
    Build Settings tab with global configuration options.
    Includes logging, themes, and region visibility settings.
    """
    general = _card("Global Settings", [
        [_t("Login wait seconds"),
         sg.Push(background_color=P["card"]),
         _inp(str(config.get("login_wait_seconds", 5)), "-WAIT-", size=(10, 1))],
        [_t("Window title template"),
         sg.Push(background_color=P["card"]),
         _inp(config.get("window_title_template", "{name} ({username})"), "-TITLE-", size=(26, 1))],
        [_t("Log level"),
         sg.Push(background_color=P["card"]),
         _combo(["DEBUG", "INFO", "WARNING", "ERROR"], config.get("log_level", "INFO"),
                "-LOGLEVEL-", size=(10, 1))],
        [_t("UI Theme"),
         sg.Push(background_color=P["card"]),
         _combo(["WizDark", "WizLight"], config.get("ui_theme", "WizDark"),
                "-THEME-", enable_events=True, size=(10, 1), pad=(6, (4, 10)))],
    ], accent=P["accent"])

    discord = _card("Discord Rich Presence", [
        [_chk("Enable Discord Rich Presence", config.get("discord_rich_presence", True),
              "-DISCORD-RPC-")],
        [_t("RPC Client ID"),
         sg.Push(background_color=P["card"]),
         _inp(config.get("discord_rpc_client_id", ""), "-DISCORD-RPC-ID-", size=(26, 1))],
        [_t("Update interval (sec)"),
         sg.Push(background_color=P["card"]),
         _inp(str(config.get("discord_rpc_update_interval", 15)),
              "-DISCORD-RPC-INTERVAL-", size=(10, 1), pad=(6, (4, 10)))],
    ], accent=P["purple"])

    def region_chk(label: str, code: str, default: bool) -> sg.Checkbox:
        return _chk(label, config.get(f"show_region_{code}", default),
                    f"-SHOW-{code.upper()}-", enable_events=True)

    regions = _card("Region Visibility", [
        [region_chk("Deutschland (DE)", "de", True),
         region_chk("United States (US)", "us", True)],
        [region_chk("France (FR)", "fr", False),
         region_chk("Italia (IT)", "it", False)],
        [region_chk("United Kingdom (GB)", "gb", False),
         region_chk("Polska (PL)", "pl", False)],
        [region_chk("España (ES)", "es", False),
         region_chk("Ελλάδα (GR)", "gr", False)],
        [_muted("Toggling a region adds or removes its tab instantly.",
                pad=((8, 6), (2, 8)))],
    ], accent=P["gold"])

    save = _card("Save", [
        [_btn("Save settings", kind="primary", size=(18, 2)),
         _muted("Ctrl+S")],
    ], accent=P["green"])

    left = sg.Column([[general], [discord]], background_color=P["panel"],
                     pad=(0, 0), vertical_alignment="top", expand_x=True)
    master = _card("Master Password", [
        [_muted("Protects your saved accounts. Changing it asks for the", pad=((8, 6), (0, 0)))],
        [_muted("current master password first.", pad=((8, 6), (0, 2)))],
        [_btn("Change master password", "-CHANGE-MASTER-", "warn", size=(24, 1),
              pad=(8, (6, 10)))],
    ], accent=P["red"])

    right = sg.Column([[regions], [master], [save]], background_color=P["panel"],
                      pad=(0, 0), vertical_alignment="top", expand_x=True)
    return _page([[left, right]])


def build_regions_tab(config: dict) -> List:
    """Build Regions tab with sub-tabs for each active region."""
    region_subtabs = []

    for region_code in REGION_META:
        if config.get(f"show_region_{region_code}", False):
            region_subtabs.append(
                _tab(
                    f"{region_code.upper()} - {REGION_META[region_code]['name']}",
                    build_region_tab(config, region_code),
                    f"-TAB-REGION-{region_code.upper()}-",
                )
            )

    if not region_subtabs:
        return _page([[_card("No regions enabled", [
            [_t("Go to the Settings tab to enable regions.", color=P["muted"],
                pad=(8, (2, 10)))],
        ], accent=P["red"])]])

    return [[_tabgroup(region_subtabs, "-REGIONS-TABGROUP-")]]


def build_logs_tab(log_viewer: Optional[LogViewer] = None) -> List:
    """Build Logs tab with live log viewer."""
    if log_viewer:
        card = _card("Live Log Viewer", [
            [
                _t("Search"),
                _inp("", "-LOG-SEARCH-", size=(30, 1)),
                _btn("Search", "-LOG-SEARCH-BTN-", "primary", size=(10, 1)),
                _btn("Clear", "-LOG-CLEAR-BTN-", size=(10, 1)),
                sg.Push(background_color=P["card"]),
                _t("Level", color=P["muted"]),
                _combo(["All", "DEBUG", "INFO", "WARNING", "ERROR"], "All",
                       "-LOG-LEVEL-", enable_events=True, size=(10, 1)),
            ],
            [_log_box((75, 20), "-LOG-OUTPUT-", no_scrollbar=False,
                      expand_x=True, expand_y=True, pad=(8, 4))],
            [
                _muted("Log file: logs/launcher.log"),
                sg.Push(background_color=P["card"]),
                _btn("Open log folder", size=(18, 1)),
                _btn("Refresh Logs", "-LOG-REFRESH-", size=(14, 1)),
                _btn("Export Log", "-LOG-EXPORT-", size=(12, 1)),
            ],
        ], accent=P["blue"], expand_y=True)
        return _page([[card]])

    card = _card("Log file", [
        [_t("logs/launcher.log", pad=(8, 4))],
        [_btn("Open log folder", size=(18, 1))],
    ], accent=P["blue"])
    return _page([[card]])


def build_wizwall_subtab(config: dict) -> List:
    """Build Wizwall sub-tab for multi-window management."""
    wizwall_enabled = config.get("wizwall_enabled", True)
    default_layout = config.get("wizwall_default_layout", "2x2")
    auto_set_resolution = config.get("wizwall_auto_set_resolution", True)
    off = not wizwall_enabled

    grid = _card("Wizwalker Multi-Window", [
        [_chk("Enable Wizwall Extension", wizwall_enabled, "-WIZWALL-ENABLED-",
              enable_events=True)],
        [
            _t("Default Layout"),
            _combo(["1x1", "2x1", "1x2", "2x2", "3x2", "3x3", "4x2", "4x3"],
                   default_layout, "-WIZWALL-LAYOUT-", size=(8, 1), disabled=off),
            _muted("(Columns x Rows)"),
        ],
        [_chk("Auto-set game resolution for layout", auto_set_resolution,
              "-WIZWALL-AUTO-RESOLUTION-",
              tooltip="Automatically configure preferences.xml before starting clients",
              disabled=off)],
        [_muted("1920x1080:  2x1 952x1070 | 2x2 952x532 | 3x2 633x532 | 3x3 633x353",
                pad=((8, 6), (2, 8)))],
    ], accent=P["accent"])

    actions = _card("Window Management", [
        [_btn("Arrange Windows", "-ARRANGE-WINDOWS-", "primary", size=(27, 1), disabled=off),
         _btn("Refresh Window List", "-REFRESH-WINDOWS-", size=(27, 1), disabled=off)],
        [_btn("Save Current Layout", "-SAVE-LAYOUT-", "success", size=(27, 1), disabled=off),
         _btn("Restore Saved Layout", "-RESTORE-LAYOUT-", "warn", size=(27, 1), disabled=off)],
        [_btn("Set Resolution for Layout", "-SET-RESOLUTION-", size=(27, 1), disabled=off),
         _muted("Apply before starting clients")],
    ], accent=P["gold"])

    windows = _card("Active Wizard101 Windows", [
        [_log_box((40, 9), "-WIZWALL-WINDOWS-", expand_x=True, pad=(8, (2, 10)))],
    ], accent=P["green"])

    tips = _card("Tips", [
        [_t("Set resolution BEFORE starting clients.",
            color=P["muted"], size=9)],
        [_t("Arrange windows once all clients are logged in.",
            color=P["muted"], size=9)],
        [_t("Borderless windows are created automatically.",
            color=P["muted"], size=9, pad=(6, (4, 10)))],
    ], accent=P["purple"])

    left = sg.Column([[grid], [actions]], background_color=P["panel"], pad=(0, 0),
                     vertical_alignment="top", expand_x=True)
    right = sg.Column([[windows], [tips]], background_color=P["panel"], pad=(0, 0),
                      vertical_alignment="top", expand_x=True)
    return _page([[left, right]])


def build_extensions_tab(config: dict) -> List:
    """Build Extensions tab with sub-tabs (currently: Wizwall)."""
    if not config.get("enable_extensions", True):
        return _page([[_card("Extensions Disabled", [
            [_t("Extensions are currently disabled.", pad=(8, (2, 2)))],
            [_t("Enable them in the configuration to access advanced features.",
                color=P["muted"], pad=(8, (2, 10)))],
        ], accent=P["red"])]])

    extension_subtabs = [
        _tab("Wizwall", build_wizwall_subtab(config), "-TAB-WIZWALL-"),
        # Future extensions can be added here.
    ]
    return [[_tabgroup(extension_subtabs, "-EXTENSIONS-TABGROUP-")]]


def build_stats_tab(stats_list: List[dict]) -> List:
    """Build Playtime Statistics tab layout."""
    headers = ["Account", "Total Playtime", "Sessions", "Avg Session"]
    rows = [
        [s["name"], s["total_playtime"], str(s["sessions"]), s["avg_session"]]
        for s in stats_list
    ]

    card = _card("Playtime Statistics", [
        [sg.Table(
            values=rows,
            headings=headers,
            max_col_width=20,
            auto_size_columns=False,
            col_widths=[20, 15, 10, 15],
            key="-STATS-TABLE-",
            num_rows=12,
            row_height=24,
            justification="left",
            background_color=P["input"],
            text_color=P["text"],
            alternating_row_color=P["card"],
            selected_row_colors=(P["on_accent"], P["accent"]),
            header_background_color=P["card_hi"],
            header_text_color=P["accent"],
            header_font=(FONT, 10, "bold"),
            header_border_width=0,
            border_width=0,
            font=(FONT, 10),
            expand_x=True,
            pad=(8, (2, 8)),
        )],
        [_btn("Refresh", kind="primary"), _btn("Export Stats", kind="success"),
         _btn("Reset All Stats", kind="danger")],
    ], accent=P["gold"])
    return _page([[card]])


def build_update_tab(config: dict, git_mode: bool) -> List:
    """
    Update page: version / update check, plus git sync when running from a checkout.

    git_mode is True for a source checkout (git pull/push available) and False for
    the installed exe (updates come from GitHub releases).
    """
    repo = str(config.get("github_repo", ""))
    card = P["card"]

    version_card = _card("Version", [
        [_t("Installed version"), sg.Push(background_color=card),
         _t(str(APP_VERSION), bold=True, pad=((6, 14), (4, 4)))],
        [_t("Update source"), sg.Push(background_color=card),
         _t(f"github.com/{repo}", color=P["muted"], pad=((6, 14), (4, 4)))],
        [_t("Mode"), sg.Push(background_color=card),
         _t("Source checkout (git)" if git_mode else "Installed app", color=P["muted"],
            pad=((6, 14), (4, 4)))],
        [_t("Not checked yet.", key="-UPD-STATUS-", color=P["muted"], size=10, bold=True,
            pad=((8, 6), (8, 4)))],
        [_btn("Check for updates", "-UPD-CHECK-", "primary", size=(20, 1)),
         _btn("Update now", "-UPD-NOW-", "success", size=(16, 1))],
        [_chk("Check for updates on start", config.get("check_for_updates", True),
              "-UPD-AUTOCHECK-", enable_events=True)],
    ], accent=P["accent"])

    if git_mode:
        git_card = _card("Git sync", [
            [_t("Remote"),
             _inp("", "-GIT-REMOTE-", size=(34, 1)),
             _btn("Set remote", "-GIT-SET-REMOTE-", size=(12, 1))],
            [_t("Press Refresh to load the git status.", key="-GIT-STATUS-",
                color=P["muted"], size=9, pad=((8, 6), (4, 4)))],
            [_btn("Pull", "-GIT-PULL-", "primary", size=(9, 1)),
             _btn("Push", "-GIT-PUSH-", "success", size=(9, 1)),
             _btn("Commit & push", "-GIT-COMMIT-PUSH-", "warn", size=(15, 1)),
             _btn("Refresh", "-GIT-REFRESH-", size=(9, 1))],
            [_chk("Auto-pull on start (fast-forward only)", config.get("git_auto_pull", False),
                  "-GIT-AUTOPULL-", enable_events=True)],
            [_chk("Auto-push after every commit", config.get("git_auto_push", False),
                  "-GIT-AUTOPUSH-", enable_events=True)],
        ], accent=P["purple"])
    else:
        git_card = _card("Git sync", [
            [_t("Git sync is available when QuickLauncher runs from a source checkout", color=P["muted"],
                size=9, pad=((8, 6), (4, 0)))],
            [_t("(git clone). This installed version updates through GitHub releases above.",
                color=P["muted"], size=9, pad=((8, 6), (0, 10)))],
        ], accent=P["purple"])

    names = " and ".join(name for name, _ in PUBLISHERS)
    publishers_card = _card("Published by", [
        [_t(names, bold=True, pad=((8, 6), (4, 2)))],
        [_t("Open a publisher's GitHub page:", color=P["muted"], size=9, pad=((8, 6), (0, 0)))],
        [_btn(f"{name} on GitHub", f"-OPEN-PUB-{i}-", size=(22, 1)) for i, (name, _) in enumerate(PUBLISHERS)],
    ], accent=P["gold"])

    return _page([[version_card], [publishers_card], [git_card]])


# ----------------------------------------------------------------------------
# Window: dashboard. Sidebar (brand, category menu, system status) on the left,
# header bar (title, search, Quicklaunch) + the selected category's page on the right.
# ----------------------------------------------------------------------------

# (id, label, subtitle) - the launcher's original tabs, unchanged.
CATEGORIES = [
    ("launch", "Launch", "Start Wizard101 instances."),
    ("accounts", "Accounts", "Manage your saved accounts."),
    ("extensions", "Extensions", "Window tools and add-ons."),
    ("regions", "Regions", "Server settings per region."),
    ("stats", "Stats", "Playtime statistics."),
    ("settings", "Settings", "Launcher configuration."),
    ("update", "Update", "Check for updates and sync with git."),
    ("logs", "Logs", "Live launcher log."),
]
_CAT_IDS = [c[0] for c in CATEGORIES]
_SEARCH_PLACEHOLDER = "Search..."
PAGE_W, PAGE_H = 850, 540

# Remembers the open category across window rebuilds (theme / region changes).
_nav_state = {"current": "launch"}


def _nav_chip(cid: str, config: dict, account_count: int) -> str:
    """Small badge on the right of a menu item (only where there is a real number)."""
    if cid == "accounts":
        return str(account_count)
    if cid == "regions":
        return str(sum(bool(config.get(f"show_region_{c}", False)) for c in REGION_META))
    return ""


def _nav_row(cid: str, label: str, selected: bool, chip: str) -> List:
    bg = P["card_hi"] if selected else P["card"]
    row = sg.Column(
        [[
            sg.Text("●", key=f"-NAVDOT-{cid}-", enable_events=True, size=(3, 2),
                    justification="center", background_color=bg,
                    text_color=P["accent"] if selected else P["muted"],
                    font=(FONT, 11, "bold"), pad=(0, 0)),
            sg.Text(label, key=f"-NAV-{cid}-", enable_events=True, size=(15, 2),
                    justification="left", background_color=bg, text_color=P["text"],
                    font=(FONT, 11, "bold"), pad=(0, 0)),
            sg.Text(chip, key=f"-NAVCHIP-{cid}-", enable_events=True, size=(5, 2),
                    justification="center", background_color=bg, text_color=P["accent"],
                    font=(FONT, 11, "bold"), pad=(0, 0)),
        ]],
        key=f"-NAVROW-{cid}-", pad=(0, 3), background_color=P["panel"],
    )
    # pin: a hidden row collapses completely and keeps its place when shown again
    return [sg.pin(row)]


def _select_category(window: sg.Window, cid: str) -> None:
    """Show one category's page and restyle the menu + header."""
    if cid not in _CAT_IDS:
        return
    label, subtitle = next((l, sub) for i, l, sub in CATEGORIES if i == cid)
    for other in _CAT_IDS:
        if other != cid:
            window[f"-PAGE-{other}-"].update(visible=False)
    window[f"-PAGE-{cid}-"].update(visible=True)
    try:
        window[f"-PAGE-{cid}-"].contents_changed()
    except Exception:
        pass

    for other in _CAT_IDS:
        selected = other == cid
        bg = P["card_hi"] if selected else P["card"]
        window[f"-NAVDOT-{other}-"].update(
            background_color=bg, text_color=P["accent"] if selected else P["muted"])
        window[f"-NAV-{other}-"].update(background_color=bg)
        window[f"-NAVCHIP-{other}-"].update(background_color=bg)

    window["-PAGE-TITLE-"].update(label)
    window["-PAGE-SUB-"].update(subtitle)
    _nav_state["current"] = cid


def _filter_categories(window: sg.Window, query: str) -> None:
    """Show only categories whose name matches the search text."""
    query = query.strip().lower()
    if query == _SEARCH_PLACEHOLDER.lower():
        query = ""
    for cid, label, _ in CATEGORIES:
        window[f"-NAVROW-{cid}-"].update(visible=(not query or query in label.lower()))


def handle_nav_event(window: sg.Window, event, values: Optional[dict] = None) -> bool:
    """
    Handle the menu / search events of the main window.

    Returns True if the event belonged to the navigation (caller should `continue`).
    """
    if not isinstance(event, str):
        return False
    for prefix in ("-NAVDOT-", "-NAVCHIP-", "-NAV-"):
        if event.startswith(prefix) and event.endswith("-"):
            cid = event[len(prefix):-1]
            if cid in _CAT_IDS:
                _select_category(window, cid)
                return True
    if event == "-NAVSEARCH-":
        _filter_categories(window, (values or {}).get("-NAVSEARCH-", ""))
        return True
    if event == "-NAVSEARCH-FOCUS":
        if (values or {}).get("-NAVSEARCH-", "") == _SEARCH_PLACEHOLDER:
            window["-NAVSEARCH-"].update("")
        window["-NAVSEARCH-"].update(text_color=P["text"])
        return True
    if event == "-NAVSEARCH-BLUR":
        if not (values or {}).get("-NAVSEARCH-", "").strip():
            window["-NAVSEARCH-"].update(_SEARCH_PLACEHOLDER, text_color=P["muted"])
        return True
    return False


def _style_inputs(window: sg.Window) -> None:
    """Flat inputs with a subtle blue border and an accent focus ring."""
    for element in window.element_list():
        widget = getattr(element, "Widget", None)
        if isinstance(element, sg.Input) and widget is not None:
            try:
                widget.configure(relief="flat", borderwidth=0, highlightthickness=1,
                                 highlightbackground=P["border"], highlightcolor=P["accent"])
            except Exception:
                pass


def _panel(rows: List[List], bg: Optional[str] = None, pad=(0, 0), **kw) -> sg.Column:
    """A bordered panel: 1px border colour around a body column."""
    inner = sg.Column(rows, background_color=bg or P["panel"], pad=(1, 1),
                      expand_x=kw.get("expand_x", False), expand_y=kw.get("expand_y", False))
    return sg.Column([[inner]], background_color=P["border"], pad=pad, **kw)


def build_window(config: dict, accounts: List[Account], log_file_path: Optional[str] = None) -> sg.Window:
    """
    Build and return the main application window (the dashboard).

    Categories (the original tabs): Launch, Accounts, Extensions, Regions,
    Stats, Settings, Logs. Event handling for the menu lives in handle_nav_event().

    Args:
        config: Application configuration dictionary
        accounts: List of Account objects to display
        log_file_path: Path to log file for live viewer

    Returns:
        PySimpleGUI Window object
    """
    current_region = config.get("current_region", "de")
    region_name = REGION_META.get(current_region, {}).get("name", current_region.upper())

    region_accounts = [acc for acc in accounts if acc.region == current_region]
    account_list = [account_display(acc) for acc in region_accounts]

    tracker = PlaytimeTracker()
    stats_list = tracker.get_accounts_stats_sorted(region_accounts, sort_by="playtime")

    log_viewer = None
    if log_file_path:
        try:
            log_viewer = LogViewer(Path(log_file_path))
        except Exception as e:
            logging.getLogger("ui").warning(f"Could not initialize log viewer: {e}")

    # Source checkout with git -> git sync is available; installed exe -> releases only
    git_mode = GitSync.for_app(Path(__file__).resolve().parent.parent) is not None

    current = _nav_state["current"] if _nav_state["current"] in _CAT_IDS else "launch"
    cur_label, cur_sub = next((l, sub) for i, l, sub in CATEGORIES if i == current)
    bg, panel, card = P["bg"], P["panel"], P["card"]

    page_layouts = {
        "launch": build_launch_tab(config, account_list),
        "accounts": build_accounts_tab(account_list),
        "extensions": build_extensions_tab(config),
        "regions": build_regions_tab(config),
        "stats": build_stats_tab(stats_list),
        "settings": build_settings_tab(config),
        "update": build_update_tab(config, git_mode),
        "logs": build_logs_tab(log_viewer),
    }

    # ---------------- sidebar ----------------
    brand = [
        sg.Text("+", size=(3, 1), justification="center", background_color=P["card_hi"],
                text_color=P["accent"], font=(FONT, 16, "bold"), pad=((14, 8), (14, 4))),
        sg.Column(
            [[_t(APP_NAME, bg=panel, size=11, bold=True, pad=(0, 0))],
             [_t(f"Version {APP_VERSION}", bg=panel, color=P["muted"], size=8, pad=(0, 0))]],
            background_color=panel, pad=((0, 10), (10, 2)),
        ),
    ]
    menu_label = [_t("MENU", bg=panel, color=P["muted"], size=8, bold=True, pad=((16, 6), (10, 4)))]
    nav_rows = [
        _nav_row(cid, label, cid == current, _nav_chip(cid, config, len(account_list)))
        for cid, label, _ in CATEGORIES
    ]
    nav_list = [sg.Column(nav_rows, key="-NAVLIST-", background_color=panel,
                          pad=((12, 12), (0, 0)), vertical_alignment="top",
                          expand_x=True, expand_y=True)]

    status_card = _panel(
        [
            [_t("SYSTEM STATUS", bg=card, color=P["muted"], size=8, bold=True, pad=((10, 6), (8, 2)))],
            [sg.Image(data=_gradient_png(170, 4, P["accent"], P["purple"], 2),
                      background_color=card, pad=((10, 10), (0, 6)))],
            [_t("Region", bg=card, color=P["muted"], size=9, pad=((10, 6), (2, 2))),
             sg.Push(background_color=card),
             _t(region_name.upper(), bg=card, color=P["green"], size=9, bold=True, pad=((6, 10), (2, 2)))],
            [_t("Accounts", bg=card, color=P["muted"], size=9, pad=((10, 6), (2, 2))),
             sg.Push(background_color=card),
             _t(str(len(account_list)), bg=card, size=9, bold=True, pad=((6, 10), (2, 2)))],
            [_t("Theme", bg=card, color=P["muted"], size=9, pad=((10, 6), (2, 8))),
             sg.Push(background_color=card),
             _t("Dark" if config.get("ui_theme", "WizDark") == "WizDark" else "Light",
                bg=card, size=9, bold=True, pad=((6, 10), (2, 8)))],
        ],
        bg=card, pad=((12, 12), (8, 12)), expand_x=True,
    )

    sidebar = _panel(
        [brand, menu_label, nav_list, [status_card]],
        bg=panel, pad=((12, 8), (12, 6)), expand_y=True,
    )

    # ---------------- header bar ----------------
    search = sg.Input(
        _SEARCH_PLACEHOLDER, key="-NAVSEARCH-", enable_events=True,
        background_color=P["input"], text_color=P["muted"], border_width=1,
        font=(FONT, 10), size=(24, 1), pad=((6, 10), (12, 12)),
    )
    header = _panel(
        [[
            sg.Column(
                [[_t(cur_label, bg=panel, size=14, bold=True, key="-PAGE-TITLE-", pad=((16, 6), (10, 0)))],
                 [_t(cur_sub, bg=panel, color=P["muted"], size=9, key="-PAGE-SUB-", pad=((16, 6), (0, 10)))]],
                background_color=panel, pad=(0, 0),
            ),
            sg.Push(background_color=panel),
            search,
            _btn("Quicklaunch", "F1", "primary", size=(13, 1), bg=panel, pad=((0, 14), (10, 10))),
        ]],
        bg=panel, pad=((0, 12), (12, 6)), expand_x=True,
    )

    # ---------------- content ----------------
    pages = [
        sg.Column(
            page_layouts[cid], key=f"-PAGE-{cid}-", visible=(cid == current),
            scrollable=True, vertical_scroll_only=True,
            background_color=panel, size=(PAGE_W, PAGE_H),
            pad=(0, 0), expand_x=True, expand_y=True,
        )
        for cid, _, _ in CATEGORIES
    ]
    content = _panel(
        [pages], bg=panel, pad=((0, 12), (0, 6)), expand_x=True, expand_y=True,
    )

    main_col = sg.Column([[header], [content]], background_color=bg, pad=(0, 0),
                         expand_x=True, expand_y=True)

    status = [
        sg.StatusBar(
            "Ready", key="-STATUS-",
            background_color=P["card"], text_color=P["muted"],
            font=(FONT, 9), relief=sg.RELIEF_FLAT,
            expand_x=True, pad=((12, 12), (0, 4)),
        ),
    ]
    hints = [
        _t("F1 Quicklaunch  -  F2 Multi-Instance  -  F3 Auto-Login  -  F4 Minimize  "
           "-  Ctrl+S Save  -  Ctrl+E Export Stats",
           bg=bg, color=P["muted"], size=8, pad=((14, 12), (0, 8))),
    ]

    layout = [[sidebar, main_col], status, hints]

    icon_path = resolve_icon_path(config)
    window = sg.Window(
        APP_NAME, layout, finalize=True, icon=icon_path,
        background_color=bg, margins=(0, 0), resizable=True,
    )
    window.set_min_size(window.size)

    # Centre the menu text vertically inside its 2-line-high cells
    for cid in _CAT_IDS:
        window[f"-NAVDOT-{cid}-"].Widget.configure(anchor="center")
        window[f"-NAV-{cid}-"].Widget.configure(anchor="w")
        window[f"-NAVCHIP-{cid}-"].Widget.configure(anchor="center")

    _style_inputs(window)

    # Don't let the search box grab the initial focus (that would wipe its placeholder)
    window.TKroot.focus_set()
    window.refresh()

    # Placeholder behaviour for the search box
    window["-NAVSEARCH-"].bind("<FocusIn>", "FOCUS")
    window["-NAVSEARCH-"].bind("<FocusOut>", "BLUR")

    # ================== KEYBOARD SHORTCUTS ==================
    window.bind('<F1>', 'F1')      # Quicklaunch
    window.bind('<F2>', 'F2')      # Multi-Instance
    window.bind('<F3>', 'F3')      # Auto-Login
    window.bind('<F4>', 'F4')      # Minimize to tray
    window.bind('<Control-e>', 'Ctrl+E')   # Export Stats
    window.bind('<Control-s>', 'Ctrl+S')   # Save Settings

    return window
