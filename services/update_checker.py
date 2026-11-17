"""Automatic update checking and version management."""

import os
import logging
import requests
from pathlib import Path
from typing import Optional, Dict
from packaging import version as pkg_version


class UpdateChecker:
    """Check for new versions from GitHub releases."""

    def __init__(self, current_version: str, repo: str = "Kayaniu-xD/QuickLauncher"):
        """
        Initialize update checker.
        
        Args:
            current_version: Current app version (e.g., "0.2.0")
            repo: GitHub repository (owner/name)
        """
        self.logger = logging.getLogger("updater")
        self.current_version = current_version
        self.repo = repo
        self.github_api_url = f"https://api.github.com/repos/{repo}/releases/latest"

    def _update_from_release(self, release: Dict) -> Optional[Dict]:
        """Update info for a GitHub release dict if it is newer than us, else None."""
        new_version = str(release.get("tag_name", "")).lstrip("v")
        if not new_version or not self._is_newer_version(new_version):
            return None
        download_url = self._pick_installer_url(release.get("assets", []))
        if not download_url:
            download_url = release.get("html_url")  # fall back to the release page
        self.logger.info(f"New version available: {new_version}")
        return {
            "new_version": new_version,
            "download_url": download_url,
            "changelog": release.get("body") or "No changelog provided",
            "published_at": release.get("published_at"),
        }

    def check_for_updates(self) -> Optional[Dict]:
        """
        Check GitHub for the latest release.

        Returns:
            Dict with update info if a newer version is available, None otherwise:
                {"new_version", "download_url", "changelog", "published_at"}
        """
        result = self.check_status()
        return result["info"]

    @staticmethod
    def _pick_installer_url(assets: list) -> Optional[str]:
        """Prefer the Setup installer; otherwise any .exe asset."""
        exes = [a for a in assets if str(a.get("name", "")).lower().endswith(".exe")]
        for asset in exes:
            if "setup" in asset["name"].lower():
                return asset["browser_download_url"]
        return exes[0]["browser_download_url"] if exes else None

    def check_status(self) -> Dict:
        """
        Ask GitHub for the latest release and say *why* there is or isn't an update.

        Returns {"state": "update" | "current" | "none" | "error", "message": str,
                 "info": update dict or None}
        """
        try:
            response = requests.get(self.github_api_url, timeout=8)
            if response.status_code == 404:
                return {"state": "none", "info": None,
                        "message": "No release published for this repository yet."}
            response.raise_for_status()
            release = response.json()
        except requests.exceptions.RequestException as exc:
            self.logger.debug(f"Update check failed: {exc}")
            return {"state": "error", "info": None,
                    "message": "Could not reach GitHub. Check your internet connection."}
        except ValueError as exc:  # bad JSON
            self.logger.error(f"Update checker error: {exc}")
            return {"state": "error", "info": None, "message": "Unexpected answer from GitHub."}

        info = self._update_from_release(release)
        if info:
            return {"state": "update", "info": info,
                    "message": f"Update available: v{info['new_version']}"}
        return {"state": "current", "info": None,
                "message": f"You are up to date (v{self.current_version})."}

    @staticmethod
    def fresh_process_env() -> dict:
        """
        Environment for starting the installer (and, through it, the new QuickLauncher).

        A PyInstaller one-file exe unpacks itself into a temp folder and tells every
        child process about it through _PYI_* / _MEIPASS2 variables. A child that
        inherits them tries to reuse that folder - which is deleted as soon as this
        process exits - and fails with "Failed to load Python DLL ...\python312.dll".
        Dropping those variables and asking the bootloader to reset makes the new
        exe start as a completely fresh process.
        """
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("_PYI_") and k != "_MEIPASS2"}
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        return env

    @staticmethod
    def download_installer(url: str, dest_dir: Path, progress=None) -> Path:
        """
        Download a release installer into dest_dir and return its path.

        Only https://github.com/... URLs are accepted (release assets), so a
        tampered API response can't point the updater at an arbitrary host.
        """
        if not url.startswith("https://github.com/"):
            raise ValueError("Refusing to download from a non-GitHub URL.")
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = Path(url.split("?")[0]).name or "QuickLauncher-Setup.exe"
        target = dest_dir / name
        partial = target.with_name(target.name + ".part")
        with requests.get(url, stream=True, timeout=30) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            done = 0
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    handle.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(done, total)
        partial.replace(target)
        return target

    def _is_newer_version(self, new_version: str) -> bool:
        """
        Compare versions using semantic versioning.
        
        Args:
            new_version: Version string to compare (e.g., "0.3.0")
            
        Returns:
            True if new_version > current_version
        """
        try:
            current = pkg_version.parse(self.current_version)
            latest = pkg_version.parse(new_version)
            return latest > current
        except Exception:
            return False

    def get_changelog(self, version: str) -> Optional[str]:
        """
        Get changelog for specific version.
        
        Args:
            version: Version to get changelog for
            
        Returns:
            Changelog text or None
        """
        try:
            url = f"https://api.github.com/repos/{self.repo}/releases/tags/v{version}"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            release = response.json()
            return release.get("body", "No changelog available")
        except Exception as e:
            self.logger.warning(f"Failed to fetch changelog for {version}: {e}")
            return None

    @staticmethod
    def format_release_info(update_info: Dict) -> str:
        """
        Format update info for display.
        
        Args:
            update_info: Update info from check_for_updates()
            
        Returns:
            Formatted string
        """
        return (
            f"New version available: {update_info['new_version']}\n\n"
            f"Changelog:\n{update_info['changelog']}\n\n"
            f"Download: {update_info['download_url']}"
        )
