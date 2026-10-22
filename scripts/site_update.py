#!/usr/bin/env python3
"""Update the site, and put it back when an update goes wrong.

The one implementation behind both ways in: `scripts/update.sh` on the server
and Admin → Update. Standard library only, and it never imports the app —
revert has to work precisely when the code it is reverting away from will not
start.

  python3 scripts/site_update.py update [--no-wait]
  python3 scripts/site_update.py revert [--yes] [--to SNAPSHOT_DIR]
  python3 scripts/site_update.py snapshot [--label LABEL]
  python3 scripts/site_update.py list

Every update first writes a restore point under var/backups/<UTC stamp>/: the
database (copied through SQLite's backup API, so a live write-ahead log is
included) and the git commit it went with. Snapshots are never overwritten;
the newest UPDATE_KEEP_SNAPSHOTS (default 10) are kept. Safety copies — taken
before a revert (-pre-revert) or by the admin panel's archive restore
(-pre-restore) — record live state that was about to be replaced, so they are
never pruned and never chosen as a revert target.
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE = "cloudflared-launch.service"
# The revision update.sh has always stamped when a database restored from a
# raw copy carries no alembic_version.
BASELINE = "4a1b2c3d4e5f"
_SAFETY = re.compile(r"-pre-(revert|restore)(-\d+)?$")


class UpdateError(Exception):
    pass


def say(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def _setting(root: Path, name: str) -> str:
    """A setting from the environment, else from the project's .env."""
    if os.environ.get(name):
        return os.environ[name]
    env = root / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, _, val = line.partition("=")
            if key.strip() == name:
                return val.strip().strip('"').strip("'")
    return ""


def database_path(root: Path) -> Path | None:
    """The SQLite database file, or None for any other database."""
    url = _setting(root, "DATABASE_URL")
    if not url:
        return root / "instance" / "app.db"
    if not url.startswith("sqlite:///"):
        return None
    path = Path(url[len("sqlite:///"):])
    return path if path.is_absolute() else root / path


def backups_dir(root: Path) -> Path:
    return root / "var" / "backups"


# ---------------------------------------------------------------------------
# Running things
# ---------------------------------------------------------------------------

def run(root: Path, *cmd: str, check: bool = True) -> str:
    """Run a command in the project, returning its output; raise with that
    output when it fails and *check* is set."""
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    out = (proc.stdout + proc.stderr).strip()
    if check and proc.returncode != 0:
        raise UpdateError(f"{' '.join(cmd)} failed:\n{out}")
    return out


def sqlite_copy(src: Path, dst: Path) -> None:
    """Copy one SQLite database onto another through the backup API, which
    reads a live write-ahead log and writes through the destination's own
    journal. A plain file copy does neither: it misses recent writes, and a
    stale -wal left beside the destination is replayed over it on next open."""
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def is_safety_copy(path: Path) -> bool:
    return bool(_SAFETY.search(path.name))


def snapshot(root: Path, label: str = "") -> Path:
    """Write a new restore point and return its directory."""
    base = backups_dir(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{label}" if label else stamp
    dest = base / name
    n = 1
    while dest.exists():
        dest = base / f"{name}-{n}"
        n += 1
    dest.mkdir(parents=True)

    db = database_path(root)
    if db is not None and db.exists():
        sqlite_copy(db, dest / "app.db")
    else:
        say("  No SQLite database found — this restore point records the code only.")
    head = run(root, "git", "rev-parse", "HEAD", check=False)
    (dest / "git-head").write_text(head if re.fullmatch(r"[0-9a-f]{40}", head) else "")
    return dest


def restore_points(root: Path) -> list[Path]:
    """Snapshots a revert may use, newest first: not safety copies, and
    holding a database."""
    base = backups_dir(root)
    if not base.exists():
        return []
    return [d for d in sorted(base.iterdir(), reverse=True)
            if d.is_dir() and not is_safety_copy(d) and (d / "app.db").exists()]


def prune(root: Path, keep: int | None = None) -> None:
    """Keep the newest *keep* update snapshots; safety copies are never pruned."""
    import shutil

    keep = keep if keep is not None else int(_setting(root, "UPDATE_KEEP_SNAPSHOTS") or 10)
    base = backups_dir(root)
    if not base.exists():
        return
    ours = [d for d in sorted(base.iterdir(), reverse=True)
            if d.is_dir() and not is_safety_copy(d)]
    for old in ours[keep:]:
        shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

def _port(root: Path) -> str:
    return _setting(root, "PORT") or "5005"


def restart(root: Path, *, wait: bool) -> None:
    """Restart the site. *wait* is for a terminal: clear a stray process off
    the port, restart, and report whether it came up. Without it the restart
    is only queued, which is what the admin page needs — the restart stops
    the very process asking for it."""
    if not wait:
        run(root, "systemctl", "--user", "restart", "--no-block", SERVICE)
        return
    run(root, "fuser", "-k", f"{_port(root)}/tcp", check=False)
    time.sleep(1)
    run(root, "systemctl", "--user", "restart", SERVICE)
    _report_running(root)


def _report_running(root: Path) -> None:
    for _ in range(10):
        time.sleep(1)
        if run(root, "systemctl", "--user", "is-active", SERVICE, check=False) == "active":
            say("✓ The site is running.")
            return
    raise UpdateError(
        f"The site did not start. See: journalctl --user -u {SERVICE} -n 50\n"
        f"To put the last version back: scripts/update.sh --revert")


# ---------------------------------------------------------------------------
# Update and revert
# ---------------------------------------------------------------------------

def migrate(root: Path) -> None:
    # Stamped only when the command worked and found no revision (a database
    # restored from a raw copy). If it failed, the app could not even be
    # asked, and stamping would roll a current database back to the baseline
    # and replay every migration over it.
    current = run(root, "uv", "run", "flask", "--app", "wsgi:app", "db", "current")
    if not re.search(r"^[0-9a-f]{12}", current, re.M):
        run(root, "uv", "run", "flask", "--app", "wsgi:app", "db", "stamp", BASELINE)
    run(root, "uv", "run", "flask", "--app", "wsgi:app", "db", "upgrade")


def update(root: Path = ROOT, *, wait: bool = True) -> Path:
    """Restore point → pull → dependencies → migrate → restart."""
    say("→ Writing a restore point…")
    point = snapshot(root)
    prune(root)
    say(f"✓ Restore point: {point.relative_to(root)}")

    say("→ Pulling the new version…")
    # Fast-forward only: a server whose checkout has drifted stops here,
    # untouched, rather than merging something nobody reviewed.
    say(run(root, "git", "pull", "--ff-only"))
    say("→ Installing dependencies…")
    run(root, "uv", "sync", "--inexact")
    say("→ Applying database migrations…")
    migrate(root)
    say("✓ Migrations applied.")
    import shutil
    if not (shutil.which("tectonic") or (Path.home() / ".local/bin/tectonic").exists()):
        say("⚠ tectonic not found: invoices, receipts and the booklet cannot be "
            "generated until scripts/install-tectonic.sh is run.")

    say("→ Restarting…")
    restart(root, wait=wait)
    say(f"  To put the previous version back: scripts/update.sh --revert")
    return point


def revert(root: Path = ROOT, *, to: Path | None = None, assume_yes: bool = False) -> None:
    """Put back the code and database of a restore point (default: newest).

    The service is stopped first, so nothing writes to the database while it
    is replaced; the database as it stands is kept as a -pre-revert copy, so a
    revert can itself be undone.
    """
    legacy = backups_dir(root) / "db.bak"
    if to is not None:
        point_db, head_file = to / "app.db", to / "git-head"
    else:
        points = restore_points(root)
        if points:
            point_db, head_file = points[0] / "app.db", points[0] / "git-head"
        elif legacy.exists():
            point_db, head_file = legacy, backups_dir(root) / "git-head"
        else:
            raise UpdateError(f"No restore point with a database under "
                              f"{backups_dir(root)} — nothing to revert to.")
    if not point_db.exists():
        raise UpdateError(f"{point_db} does not exist — nothing to revert to.")
    db = database_path(root)
    if db is None:
        raise UpdateError("The database is not SQLite; restore it from your own dump.")
    head = head_file.read_text().strip() if head_file.exists() else ""
    when = datetime.fromtimestamp(point_db.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

    say(f"⚠ This puts back the database and code saved at {when} "
        f"({point_db.parent.name}).")
    say("  Anything written to the site since then is set aside, not deleted.")
    if not assume_yes:
        try:
            answer = input("Continue? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "y":
            raise UpdateError("Revert cancelled — nothing changed.")

    say("→ Stopping the site…")
    run(root, "systemctl", "--user", "stop", SERVICE, check=False)
    if db.exists():
        aside = snapshot(root, "pre-revert")
        say(f"✓ The database as it stood is kept at {aside.relative_to(root)}")
    db.parent.mkdir(parents=True, exist_ok=True)
    sqlite_copy(point_db, db)
    say("✓ Database put back.")

    if head:
        run(root, "git", "reset", "--hard", head)
        say(f"✓ Code put back to {head[:12]}.")
        out = run(root, "uv", "sync", "--inexact", check=False)
        if "error" in out.lower():
            say(f"⚠ Dependencies may not match that version:\n{out}")
    else:
        say("  No code version recorded with this restore point — code left as it is.")

    say("→ Starting the site…")
    run(root, "systemctl", "--user", "start", SERVICE, check=False)
    _report_running(root)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="site_update")
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("update")
    up.add_argument("--no-wait", action="store_true",
                    help="queue the restart instead of waiting for it")
    rv = sub.add_parser("revert")
    rv.add_argument("--yes", action="store_true")
    rv.add_argument("--to", type=Path)
    sn = sub.add_parser("snapshot")
    sn.add_argument("--label", default="")
    sub.add_parser("list")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "update":
            update(ROOT, wait=not args.no_wait)
        elif args.cmd == "revert":
            revert(ROOT, to=args.to, assume_yes=args.yes)
        elif args.cmd == "snapshot":
            say(str(snapshot(ROOT, args.label)))
        else:
            for p in restore_points(ROOT):
                head = (p / "git-head").read_text().strip()[:12] if (p / "git-head").exists() else ""
                say(f"{p.name}  {head}")
    except UpdateError as e:
        say(f"✗ {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
