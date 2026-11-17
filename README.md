# QuickLauncher v0.3.10

Open source Wizard101 launcher with quick start, multi-instance, auto-login, playtime tracking, multi-window management, and extensible wizwalker integrations.

##  Features

### Core Launcher
-  **Quicklaunch** - Start instance instantly with one click
-  **Multi-Instance** - Launch multiple instances simultaneously (up to 8 regions)
-  **Auto-Login** - Automatic account injection via wizwalker
-  **Region Support** - 8 configurable regions (DE, US, FR, IT, GB, PL, ES, GR)
-  **Account Management** - Add, edit, delete encrypted accounts with master password protection

### Extensions System
-  **Wizwall** - Multi-window management with borderless windows
  -  **Grid Layouts** - 7 preset layouts (2x1, 2x2, 3x3, 4x3, 4x4, 2x3, 3x2)
  -  **Borderless Windows** - Pixel-perfect rendering without title bars
  -  **Auto-Resolution** - Automatically sets game resolution for window size
  -  **Save/Restore Layouts** - Persist window arrangements
  -  **Account Selection** - Select which accounts to launch via checkboxes
-  **Extensible Architecture** - Easy to add new wizwalker-based extensions
-  **Toggle Extensions** - Enable/disable features via config

### Playtime Tracking
-  **Session Tracking** - Automatically track playtime when using auto-login
-  **Statistics** - View total playtime, session count, and average session duration per account
-  **Export Stats** - Save playtime statistics to file
-  **Reset Stats** - Clear all playtime data (with confirmation)

### Region Management
-  **Per-Region Configuration** - Custom install paths, servers, and ports per region
-  **Live Region Toggle** - Show/hide region tabs without restart
-  **One-Click Region Switch** - Change current region instantly
-  **Default Settings** - Reset regions to factory defaults

### User Interface
-  **App Icon** - Wizard101 themed icon in window and taskbar
-  **Modern Dark Theme** - WizDark theme with color-coded UI elements
-  **Tabbed Interface** - Launch, Accounts, Settings, Logs, and Stats tabs
-  **Configurable Options** - Window title template, foreground behavior, logging level
-  **Log Viewer** - Quick access to application logs

## Requirements
- Windows 10+
- Python 3.11 or 3.12 (3.12 recommended)
- (Optional) EU wizwalker fork for auto-login and extensions (recommended for DE/EU servers)
- (Optional) pywin32 for window management (auto-installed)

## Download

**Pre-built Executable** (no Python required):
- Download `QuickLauncher-Setup-<version>.exe` from the [Releases](https://github.com/Kayaniu-xD/QuickLauncher/releases) page and run it
  (installs to Program Files; all your data stays in the install folder's `data` and `logs` folders)
- All dependencies included

**From Source** (for developers):
See Installation section below

## Installation

### Option 1: Pre-built Executable (Recommended)
```bash
# Download QuickLauncher.exe from releases
# Double-click to run - that's it!
```

### Option 2: From Source
```bash
# Clone or download the repository
git clone https://github.com/Kayaniu-xD/QuickLauncher.git
cd QuickLauncher

# Create virtual environment (optional but recommended)
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Option 3: Build Your Own Executable
```bash
# After installing from source
python build.py
# Executable will be in dist/QuickLauncher.exe
```

## Quick Start

```bash
# Launch the application
python main.py
```

1. Create or unlock your master password on first run
2. Add accounts in the **Accounts** tab
3. Select region from **Launch** tab (shows current region)
4. Choose between:
   - **Quicklaunch** - Start one instance
   - **Start N** - Start multiple plain instances
   - **Start + Auto Login** - Start with automatic login and playtime tracking
## Documentation

- [Usage Guide](documentation/USAGE.md) - How to use all features
- [Configuration Reference](documentation/CONFIGURATION.md) - All config options
- [Playtime Tracker](PLAYTIME_TRACKER.md) - Session tracking details

## Versions

**Every update raises the version by 0.0.1** (0.3.2 -> 0.3.3 -> 0.3.4; no carry, so 0.3.9 -> 0.3.10). `APP_VERSION` in `core/config.py` is the only place to change; the installer, the release tag and the README title follow it.

- `python scripts/bump_version.py` does it (config, README title, changelog heading).
- The Update page's **Commit & push** does it automatically - but only when something was actually committed, and not twice if you already raised the version yourself.

A new version makes the GitHub workflow publish a new release, and the in-app **Update** button then offers it to everyone on an older version.

## Backup & restore

**Settings > Backup** exports your settings and accounts into one `.qlbackup` file, e.g. to move to a new PC.

- **Export** asks for your current master password. The whole file is encrypted with it (PBKDF2 + Fernet, like the accounts file) - nothing in it is readable without the password. Keep the file and the password safe.
- **Import** asks for the password the backup was made with, then lets you *add missing accounts* (keeps your existing ones) or *replace all accounts*, and optionally import the settings. Accounts are re-saved under **your own** master password, so a new PC can keep a different one.
- A backup file is treated as untrusted: install paths, login servers, launch arguments, the update source and Discord webhooks are **never** imported.

## Updating & Git sync

Open **Update** in the menu.

- **Check for updates / Update now** - compares your version with the latest GitHub release of `github_repo` (default `Kayaniu-xD/QuickLauncher`). With the installed app it downloads the new Setup and runs it (your accounts and settings are kept). Turn off *Check for updates on start* to stop the automatic check.
- **Git sync** (only when running from a `git clone`): set the remote, **Pull** (fast-forward only, never rewrites your work), **Push**, or **Commit & push** in one click.
  - *Auto-pull on start*: fast-forward pull every time the launcher starts.
  - *Auto-push after every commit*: installs a `post-commit` git hook that pushes in the background. Switching it off removes the hook; a hook of your own is never overwritten.

Accounts, `config.json` and logs are in `.gitignore` and are never committed.

## Automatic releases

Every push to `main` builds the Setup installer on GitHub Actions and attaches **only the Setup** (no separate exe) to the release `v<APP_VERSION>` (`APP_VERSION` is in `core/config.py`). Same version = the files of that release are replaced; **bump `APP_VERSION` to publish a new release** - the in-app *Update* button then offers it to everyone on an older version. The build runs from the repository, so it never contains your local accounts or settings. A final workflow step **verifies** that `QuickLauncher-Setup-<version>.exe` is really attached to the release and that the release tag points at the pushed commit - if not, the run fails (red) instead of silently shipping a release without the Setup.

## Credits

QuickLauncher is published by:

- **Kayaniu-xD** - <https://github.com/Kayaniu-xD/QuickLauncher>
- **xLordTime** - <https://github.com/xLordTime/wiz-q-launcher> (second publisher)

Both are shown on the **Update** page in the app and in the installer.

## Project Structure

```text
QuickLauncher/
  main.py                      # Root run entry point
  build.py                     # Root build entry point
  app/                         # App runtime modules
    main.py                    # Event loop and app controller
    ui.py                      # UI components and theme
  core/                        # Core config and logging
    config.py
    logging_utils.py
  security/                    # Encryption and account management
    crypto.py
  services/                    # Launching, tracking, utilities
    launcher.py
    playtime_tracker.py
    performance_monitor.py
    log_viewer.py
    update_checker.py
    issue_reporter.py
    window_manager.py
  integrations/                # External integrations
    discord_integration.py
  scripts/                     # Build/maintenance scripts
    build.py
  documentation/               # Extended documentation
```
## Configuration

The launcher creates config.json automatically. Common settings:

```json
{
  "current_region": "de",
  "show_region_de": true,
  "show_region_us": true,
  "region_install_de": "",
  "region_server_de": "login-de.eu.wizard101.com",
  "region_port_de": 12000,
  "login_wait_seconds": 5,
  "window_title_template": "{name} ({username})",
  "foreground_on_login": true,
  "log_level": "INFO"
}
```

See [Configuration Guide](documentation/CONFIGURATION.md) for all options.

## Usage Examples

### Quick Launch
1. Go to **Launch** tab
2. Click **Quicklaunch** to start one instance
3. Wizard101 launches with current region settings

### Multi-Instance Launch with Wizwall
1. Go to **Extensions** -> **Wizwall** tab
2. Select accounts from the list (shows live counter)
3. Choose grid layout (2x2, 3x3, etc.)
4. Enable "Auto Set Resolution" for pixel-perfect windows
5. Click **Start Instances** or **Start + Auto Login**
6. Windows are automatically arranged in borderless grid layout

### Region Switching
1. Click on region tab (DE, US, FR, etc.)
2. (Optional) Edit install path, server, port
3. Click **Set as Current** button
4. Launcher switches to that region instantly

### Playtime Tracking
1. Select accounts in Launch tab
2. Click **Start + Auto Login**
3. Sessions are tracked automatically
4. View stats in **Stats** tab
5. Export or reset as needed

## Notes

- The UI uses PySimpleGUI OSS package (PySimpleGUI-4-foss). You can upgrade to the Hobbyist build if desired.
- Auto-login feature requires wizwalker package installed.
- Playtime tracking only works with **Start + Auto Login** feature.
- Account passwords are AES-256 encrypted with a master password.
- All configuration persists in config.json and is human-readable.

## Troubleshooting

**Auto-login not working?**
- Install EU fork: pip install git+https://github.com/Lapridox/wizwalker.git@2cab70d98c41ad53aef1d38e3caf3be208eeea1c
- Check logs in **Logs** tab for error messages

**Playtime not tracking?**
- Only tracks with **Start + Auto Login**
- Ensure instances launched through the launcher
- Close instances normally (don't force-close)

**Region settings resetting?**
- Check that config.json is writable
- Verify region code matches REGION_META in config.py

## License

MIT License - See LICENSE file for details

## Version History

- **v0.4.0** (2026-02-16) - Extensions system, Wizwall, borderless windows, region-specific accounts, .exe builds
- **v0.3.0** (2026-02-15) - Multi-window manager, grid layouts
- **v0.2.0** (2026-02-14) - Playtime tracking, region management, stats
- **v0.1.0** (2026-02-13) - Initial release with quicklaunch and auto-login




