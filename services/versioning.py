"""Version bumping: every update raises the patch number by one (0.3.2 -> 0.3.3).

The single source of truth is APP_VERSION in core/config.py. Bumping also keeps
the README title in sync and turns the changelog's "Unreleased" notes into a
dated release heading. Pushing a new version makes the GitHub workflow create a
new release, which the in-app Update button then offers to older installs.

Numbers don't carry: 0.3.9 -> 0.3.10 (semantic version, not a decimal).
"""

import re
from datetime import date
from pathlib import Path
from typing import Optional

_VERSION_LINE = re.compile(r'^(APP_VERSION\s*=\s*")(\d+)\.(\d+)\.(\d+)(")', re.MULTILINE)
_README_TITLE = re.compile(r"^(# QuickLauncher v)\d+\.\d+\.\d+", re.MULTILINE)


def config_path(root: Path) -> Path:
    return Path(root) / "core" / "config.py"


def read_version(root: Path) -> str:
    match = _VERSION_LINE.search(config_path(root).read_text(encoding="utf-8"))
    if not match:
        raise ValueError("APP_VERSION not found in core/config.py")
    return ".".join(match.group(i) for i in (2, 3, 4))


def next_patch(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def bump_patch(root: Path, today: Optional[date] = None) -> str:
    """Raise the version by 0.0.1 everywhere it is written. Returns the new version."""
    root = Path(root)
    old = read_version(root)
    new = next_patch(old)

    cfg = config_path(root)
    cfg.write_text(_VERSION_LINE.sub(lambda m: f"{m.group(1)}{new}{m.group(5)}",
                                     cfg.read_text(encoding="utf-8"), count=1), encoding="utf-8")

    readme = root / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        readme.write_text(_README_TITLE.sub(lambda m: f"{m.group(1)}{new}", text, count=1), encoding="utf-8")

    changelog = root / "CHANGELOG.md"
    if changelog.exists():
        text = changelog.read_text(encoding="utf-8")
        marker = "## Unreleased\n"
        heading = f"## v{new} ({(today or date.today()).isoformat()})\n"
        if marker in text and heading not in text:
            # Notes collected under "Unreleased" now belong to this release; start a fresh section.
            changelog.write_text(text.replace(marker, f"{marker}\n{heading}", 1), encoding="utf-8")
    return new
