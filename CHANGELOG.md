# Changelog

## Unreleased

## v0.3.17 (2026-11-17)
### Added
- Five new UI themes: Midnight, Forest, Crimson, Amethyst and Sand (light), next to Dark and Light.
- "Custom" theme: Settings -> Custom Colors lets you pick background, panels, cards, text and accent colours (colour picker or hex code) and applies them; Reset restores the defaults. Everything else (borders, inputs, muted text, ...) is derived automatically.

## v0.3.16 (2026-11-17)
### Changes
- Launch page order is now: Launch, Accounts to launch, Region (then Window options).

## v0.3.15 (2026-11-17)
### Fixed
- After an install/update Explorer sometimes kept showing the old cached icon on shortcuts. The Setup now tells Explorer to refresh its icons when it finishes.

## v0.3.14 (2026-11-17)
### Changes
- The wizard icon is also shown in the launcher's sidebar (instead of the "+" tile).

## v0.3.13 (2026-11-17)
### Changes
- The mascot is more transparent by default (45 % instead of 70 %; an unchanged old default is moved over once). New slider under Settings -> "Mascot opacity (%)": it changes the mascot live while you drag and is saved when you let go.

## v0.3.12 (2026-11-17)
### Changes
- New app icon (wizard with staff). It is used for the exe, the installer and every window, including the master-password prompt.
- Auto-login reacts faster: shorter polling and pauses (game window every 0.2 s instead of 0.5 s, login screen check 0.2 s, 0.4 s instead of 1 s before typing, 0.1 s between key steps). The fixed fallback "Login wait seconds" now defaults to 2 s instead of 5 s (only used when the login screen cannot be detected).
### Fixed
- "Start + Auto Login" (button and F3) no longer freezes the launcher for several seconds: waiting for the game windows now happens in the background and the status bar shows the progress.

## v0.3.11 (2026-11-17)
### Changes
- The mascot is centred in the whole window (not only the content area) and her height follows the window: she fits a normal window and grows to the window size when maximised / fullscreen (scaled with GDI+).
### Fixed
- The mascot is now kept directly above the launcher window in the z-order, so it can no longer end up hidden behind it.

## v0.3.10 (2026-11-17)
### Changes
- The mascot is twice as big (658x742 px). It is clipped to the launcher window, so it never hangs out over the desktop.
### Fixed
- Maximising the window made the mascot vanish and left a white box; the overlay now keeps following and animating in the maximised state too.

## v0.3.9 (2026-11-17)
### Fixed
- The "Failed to load Python DLL" error after an update is now fixed for real: the Setup starts QuickLauncher through a small `cmd` wrapper that resets the PyInstaller environment. 0.3.8 set that flag inside the elevated installer, but the "launch QuickLauncher" step runs from the non-elevated first process, which never saw it.

## v0.3.8 (2026-11-17)
### Fixed
- After an in-app update the new QuickLauncher failed to start with "Failed to load Python DLL ... python312.dll". The updater and the Setup now start the new exe as a fresh process (they no longer pass on the old exe's PyInstaller temp-folder variables).

## v0.3.7 (2026-11-17)
### Changes
- The dancing mascot is now bigger, centred on **every** page and drawn as a click-through, semi-transparent overlay (opacity `mascot_opacity` in `data/config.json`, default 0.7). It can be switched off under Settings -> "Show the dancing mascot".

## v0.3.6 (2026-11-17)
### Added
- Animated dancing mascot (Konata from Lucky Star, green screen removed) next to the account list; frames live in `assets/mascot/` and are bundled into the exe.

## v0.3.5 (2026-11-17)
### Changes
- Releases contain **only** `QuickLauncher-Setup-<version>.exe`; the plain `QuickLauncher.exe` is no longer uploaded (and the workflow fails if it ever is).

## v0.3.4 (2026-11-17)
### Changes
- The release workflow now verifies that the Setup installer is attached to the release for the pushed commit and fails loudly otherwise.

## v0.3.3 (2026-11-17)
### New Features
- **Versioning**: every update raises the version by 0.0.1 (`scripts/bump_version.py`; automatic in "Commit & push" on the Update page).
- **Backup & restore** (Settings > Backup): export settings + accounts to one encrypted `.qlbackup` file (needs the master password); import with merge/replace options. Dangerous settings are never imported.
- **Automatic releases**: every push to `main` builds the Setup installer + portable exe on GitHub Actions and uploads them to the release `v<APP_VERSION>`.
- **Publishers**: xLordTime credited as second publisher (installer, Update page, README, LICENSE).
- **Update page** in the menu: check GitHub releases, update the installed app with one click (downloads and runs the Setup), or `git pull` when running from a checkout.
- **Git sync**: set remote, pull (fast-forward only), push, commit & push; optional auto-pull on start and auto-push via a `post-commit` hook.
- Windows installer (`QuickLauncher-Setup-<version>.exe`), data kept in the install folder.
- Change the master password in Settings; show-password option in the account dialog.
- New dashboard design, dark grey / light themes, green/black icon.
- Login-screen detection and Tab/Enter key handling for auto-login.
### Changes
- Renamed to **QuickLauncher**. Update source now defaults to `Kayaniu-xD/QuickLauncher` (old configs pointing at `oliwi/q-launcher` are migrated).
- `config.json` and exports moved into the `data` folder (an existing config is copied over once).

## v0.3.0 (2026-02-16) - Multi-Window Manager & App Icon
### ✨ New Features
- **Multi-Window Manager**: Arrange multiple Wizard101 windows in grid layouts
  - Support for 7 preset layouts: 2x1, 1x2, 2x2, 3x2, 3x3, 4x2, 4x3
  - **wizwalker Integration**: Enhanced window detection via `get_all_wizard_handles()`
  - **Smart Fallback**: Auto-falls back to win32gui if wizwalker unavailable
  - **Window Info**: Uses wizwalker's `get_window_rectangle()` and `get_window_title()`
  - **Layout Saving**: Save and restore custom window arrangements
  - **Position Query**: Get current position/size of any window
  - Automatic window detection with account mapping
  - One-click window arrangement with configurable padding
  - "Refresh Window List" to view all active windows
  - Integration with auto-login session tracking
- **App Icon**: Added Wizard101-themed icon (icon.png)
  - Automatic PNG to ICO conversion (with Pillow if available)
  - Configurable via `app_icon_path` in config.json
  - Window icon displayed in taskbar and window frame
- **Enhanced Launch Tab**: New "Multi-Window Management" section
  - Grid layout dropdown selector
  - "Arrange Windows" and "Refresh Window List" buttons
  - Compact account selection layout (size optimized)
- **Dependencies**: Added `pywin32>=307` for window management

### 🔧 Improvements
- **wizwalker Integration Enhanced**: Window Manager now uses wizwalker APIs as primary method
  - `get_all_wizard_handles()` for accurate window detection
  - `get_window_rectangle()` for precise position/size querying
  - `get_window_title()` for window information
  - `set_foreground_window()` for reliable window focusing
- Fixed `requirements.txt` formatting (wizwalker line break issue)
- Improved error handling in main event loop
- Better module initialization with try-except wrappers

### 📝 Documentation
- Added WINDOW_MANAGER.md with complete window management guide including wizwalker integration details
- Updated README.md with window manager feature and wizwalker enhancements

### 🐛 Bug Fixes
- Fixed syntax errors in main.py event loop structure
- Fixed bind_all() AttributeError (changed to bind())
- Fixed IssueReporter backtick escape sequences
- Resolved indentation issues after error handling additions

---

## v0.2.0 (2026-02-14) - Playtime Tracking & Region Management
### ✨ New Features
- **Playtime Tracker**: Automatically track session time per account
  - Total playtime, session count, and average session duration
  - Stats export to file
  - Reset all stats functionality
- **Region Management**: Support for 8 regions with per-region configuration
  - DE (Deutschland), US (United States), FR (France), IT (Italia), GB (United Kingdom), PL (Polska), ES (España), GR (Ελλάδα)
  - Per-region tabs with custom install paths, servers, and ports
  - Live region visibility toggle (no restart needed)
  - One-click region switching
- **Enhanced UI**: New Stats tab showing playtime statistics for all accounts
- **Refactored Architecture**: Modular code structure (config, crypto, launcher, ui, logging_utils, playtime_tracker)
- **Account Features**: Edit and delete existing accounts
- **Improved Logging**: Dedicated logging_utils module with rotating logs

### 🐛 Bug Fixes
- Fixed region switching with proper window rebuild
- Fixed playtime tracking for multiple concurrent sessions
- Improved handle detection for new instances

### 📝 Documentation
- Added PLAYTIME_TRACKER.md with full tracking documentation
- Updated UI_UPDATE.md with region management details
- Added REFACTORING.md documenting code structure
- Extended USAGE.md with new features
- Expanded CONFIGURATION.md with region-specific keys

---

## v0.1.0 (2026-02-13) - Initial Release
### ✨ Initial Features
- Quicklaunch with configurable server, port, and region
- Multi-instance start with optional auto-login via wizwalker
- Encrypted account storage with master password
- Install path detection (config, default path, registry)
- Window title and foreground control for started clients
- Configurable logging with rotating logs
- PyInstaller and GitHub Actions build workflow
