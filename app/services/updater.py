"""Update-checker stub.

Hits `git ls-remote` against the configured `UPDATE_REMOTE_URL` to detect
whether a newer commit is on `UPDATE_BRANCH`. Stub: actual notification
emails are wired up in a future release. The admin panel surfaces the
current status read from `latest_status()`.

Designed so the rest of the project remains uncoupled — this can be
swapped for a richer fetch later without touching callers.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime

from flask import current_app


log = logging.getLogger(__name__)


@dataclass
class UpdateStatus:
    enabled: bool
    remote: str
    branch: str
    local_sha: str | None
    remote_sha: str | None
    behind: bool
    checked_at: datetime

    @property
    def message(self) -> str:
        if not self.enabled:
            return "Update checks are disabled (UPDATE_REMOTE_URL is unset)."
        if self.local_sha is None:
            return ("Local commit unknown — the site is probably not running "
                    "from a git checkout.")
        if self.remote_sha is None:
            return "Could not reach the upstream remote."
        if self.behind:
            return ("An update is available. Review the changelog before "
                    "running `git pull` + `uv sync` + restarting.")
        return "You are running the latest commit on this branch."


def _git(*args: str) -> str | None:
    try:
        out = subprocess.check_output(["git", *args],
                                      stderr=subprocess.STDOUT, timeout=10)
        return out.decode().strip()
    except Exception:
        log.warning("git %s failed", " ".join(args), exc_info=True)
        return None


def latest_status() -> UpdateStatus:
    cfg = current_app.config
    remote = cfg.get("UPDATE_REMOTE_URL", "")
    branch = cfg.get("UPDATE_BRANCH", "main") or "main"
    now = datetime.utcnow()

    if not remote:
        return UpdateStatus(False, "", branch, None, None, False, now)

    local = _git("rev-parse", "HEAD")
    remote_sha = None
    ls = _git("ls-remote", remote, branch)
    if ls:
        remote_sha = ls.split()[0] if ls else None

    behind = bool(local and remote_sha and local != remote_sha)
    return UpdateStatus(True, remote, branch, local, remote_sha, behind, now)
