#!/usr/bin/env python3
"""
Raise the version by 0.0.1 (e.g. 0.3.2 -> 0.3.3).

Run this once per update, before committing:
    python scripts/bump_version.py

Updates APP_VERSION in core/config.py, the README title and the changelog.
(The Update page's "Commit & push" button does this for you.)
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.versioning import bump_patch, read_version  # noqa: E402


def main() -> int:
    old = read_version(ROOT)
    new = bump_patch(ROOT)
    print(f"Version {old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
