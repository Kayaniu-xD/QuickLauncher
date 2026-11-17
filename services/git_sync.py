"""Git helpers: status, pull, push, commit+push and an optional auto-push hook.

Only meaningful when QuickLauncher runs from a git checkout (source install).
The installed .exe has no repository - there the Update page uses GitHub
releases instead (see services/update_checker.py).

Every call is a short-lived `git` subprocess with no console window, a
timeout, and terminal prompts disabled so a missing credential can never hang
the UI.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HOOK_MARKER = "# quicklauncher-auto-push"
_HOOK_BODY = f"""#!/bin/sh
{HOOK_MARKER}
# Installed by QuickLauncher: push in the background after every commit.
# Remove it from the Update page (or delete this file) to stop auto-pushing.
( git push --quiet >/dev/null 2>&1 || git push --quiet -u origin HEAD >/dev/null 2>&1 ) &
exit 0
"""

# https://..., http(s) git URLs, ssh://..., scp-like git@host:path - never something
# that starts with "-" (would be parsed as a git option).
_URL_RE = re.compile(r"^(https?://|ssh://|git@)[^\s]+$")


class GitError(Exception):
    """A git command failed (message is user-presentable)."""


def find_repo_root(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for a .git directory/file."""
    start = Path(start).resolve()
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    return None


def is_valid_remote_url(url: str) -> bool:
    return bool(_URL_RE.match(url.strip()))


class GitSync:
    """Thin wrapper around the git CLI for one repository."""

    def __init__(self, repo_dir: Path, git_executable: Optional[str] = None):
        self.logger = logging.getLogger("git")
        self.repo_dir = Path(repo_dir)
        self.git = git_executable or shutil.which("git")

    # ------------------------------------------------------------------ basics
    @classmethod
    def for_app(cls, app_root: Path) -> Optional["GitSync"]:
        """GitSync for the checkout that contains app_root, or None (installed exe / no git)."""
        if getattr(sys, "frozen", False):
            return None
        root = find_repo_root(app_root)
        sync = cls(root) if root else None
        return sync if sync and sync.git else None

    def _run(self, *args: str, timeout: int = 60) -> Tuple[int, str, str]:
        if not self.git:
            raise GitError("git is not installed or not on PATH.")
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [self.git, *args],
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} timed out after {timeout}s.") from exc
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def _ok(self, *args: str, timeout: int = 60) -> str:
        code, out, err = self._run(*args, timeout=timeout)
        if code != 0:
            raise GitError(self._error_text(err or out, args[0], code))
        return out

    @staticmethod
    def _error_text(text: str, command: str, code: int) -> str:
        """The line that actually says what went wrong ("fatal: ..."), not the noise around it."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if line.lower().startswith(("fatal:", "error:")):
                return line
        return lines[-1] if lines else f"git {command} failed (exit {code})."

    # ------------------------------------------------------------------ status
    def remote_url(self, name: str = "origin") -> str:
        code, out, _ = self._run("remote", "get-url", name)
        return out if code == 0 else ""

    def branch(self) -> str:
        code, out, _ = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return out if code == 0 else ""

    def status(self, fetch: bool = False) -> Dict:
        """Branch, remote, ahead/behind, number of changed files, last commit."""
        info: Dict = {
            "branch": self.branch(),
            "remote_url": self.remote_url(),
            "ahead": 0,
            "behind": 0,
            "dirty": 0,
            "last_commit": "",
            "has_upstream": False,
            "fetch_error": "",
        }
        if fetch and info["remote_url"]:
            code, _, err = self._run("fetch", "--quiet", timeout=30)
            if code != 0:
                info["fetch_error"] = err.splitlines()[-1] if err else "fetch failed"
        code, out, _ = self._run("status", "--porcelain")
        info["dirty"] = len([line for line in out.splitlines() if line.strip()]) if code == 0 else 0
        code, out, _ = self._run("log", "-1", "--format=%h %s")
        info["last_commit"] = out if code == 0 else ""
        code, out, _ = self._run("rev-list", "--left-right", "--count", "HEAD...@{u}")
        if code == 0 and out:
            ahead, behind = out.split()
            info.update(ahead=int(ahead), behind=int(behind), has_upstream=True)
        return info

    @staticmethod
    def describe(info: Dict) -> str:
        """One-line summary for the UI."""
        if not info.get("branch"):
            return "No commits yet."
        parts = [f"Branch {info['branch']}"]
        if not info.get("remote_url"):
            parts.append("no remote set")
        elif not info.get("has_upstream"):
            parts.append("not pushed yet")
        else:
            parts.append(f"{info['ahead']} to push")
            parts.append(f"{info['behind']} to pull")
        parts.append(f"{info['dirty']} changed file(s)" if info["dirty"] else "clean")
        if info.get("fetch_error"):
            parts.append(f"fetch failed: {info['fetch_error']}")
        return "  |  ".join(parts)

    # ----------------------------------------------------------------- actions
    def set_remote(self, url: str, name: str = "origin") -> str:
        url = url.strip()
        if not is_valid_remote_url(url):
            raise GitError("Enter a git URL like https://github.com/owner/repo.git")
        if self.remote_url(name):
            self._ok("remote", "set-url", name, url)
        else:
            self._ok("remote", "add", name, url)
        return f"Remote '{name}' set to {url}"

    def pull(self) -> str:
        """Fast-forward only: never creates merge commits or rewrites local work."""
        if not self.remote_url():
            raise GitError("No remote set - add one first.")
        out = self._ok("pull", "--ff-only", timeout=120)
        if "Already up to date" in out:
            return "Already up to date."
        summary = next((line.strip() for line in out.splitlines() if " changed" in line), None)
        return f"Updated: {summary}" if summary else "Updated."

    def push(self) -> str:
        if not self.remote_url():
            raise GitError("No remote set - add one first.")
        code, _, _ = self._run("rev-parse", "--abbrev-ref", "@{u}")
        args = ("push",) if code == 0 else ("push", "-u", "origin", "HEAD")
        out = self._ok(*args, timeout=120)
        return out.splitlines()[-1] if out else "Pushed."

    def commit_all(self, message: str) -> str:
        """Stage everything not ignored and commit (no-op if nothing changed)."""
        message = message.strip() or "Update"
        self._ok("add", "-A")
        code, _, _ = self._run("diff", "--cached", "--quiet")
        if code == 0:
            return "Nothing to commit."
        self._ok("commit", "-m", message)
        return "Committed."

    def commit_and_push(self, message: str) -> str:
        committed = self.commit_all(message)
        pushed = self.push()
        return f"{committed} {pushed}"

    # ---------------------------------------------------------- auto-push hook
    @property
    def _hook_path(self) -> Path:
        code, out, _ = self._run("rev-parse", "--git-path", "hooks/post-commit")
        path = Path(out) if code == 0 and out else Path(".git/hooks/post-commit")
        return path if path.is_absolute() else self.repo_dir / path

    def auto_push_enabled(self) -> bool:
        hook = self._hook_path
        try:
            return hook.is_file() and HOOK_MARKER in hook.read_text(encoding="utf-8")
        except OSError:
            return False

    def set_auto_push(self, enabled: bool) -> None:
        """Install/remove the post-commit hook. Never touches a hook that isn't ours."""
        hook = self._hook_path
        if enabled:
            if hook.exists() and HOOK_MARKER not in hook.read_text(encoding="utf-8", errors="replace"):
                raise GitError("A different post-commit hook already exists - not overwriting it.")
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text(_HOOK_BODY, encoding="utf-8", newline="\n")
            try:
                hook.chmod(0o755)
            except OSError:
                pass
        elif self.auto_push_enabled():
            hook.unlink()
