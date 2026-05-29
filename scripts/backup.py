#!/usr/bin/env python3
"""Daily backup script for the society-site database.

Handles both SQLite and Postgres safely:
  - SQLite:  uses the sqlite3 .backup API (safe during concurrent writes)
  - Postgres: uses pg_dump

Backups are written to ./backups/ with a date-stamped filename.
Old backups are pruned after KEEP_DAYS (default 30).

Usage:  uv run python scripts/backup.py
"""
from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTANCE_DIR = PROJECT_ROOT / "instance"
BACKUP_DIR = PROJECT_ROOT / "backups"
KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS", "30"))


def _load_env() -> None:
    """Load .env into os.environ if present."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    with open(env_file) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _database_url() -> str:
    return (os.environ.get("DATABASE_URL") or f"sqlite:///{INSTANCE_DIR / 'app.db'}").strip()


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _sqlite_path(url: str) -> Path:
    # sqlite:///relative/path  or  sqlite:////absolute/path
    prefix = "sqlite:///"
    path = url[len(prefix):]
    if path.startswith("/"):
        return Path(path)
    return PROJECT_ROOT / path


def _backup_sqlite(db_path: Path, dest: Path) -> None:
    import sqlite3
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    src.backup(dst)
    dst.close()
    src.close()


def _backup_postgres(url: str, dest: Path) -> None:
    # Parse a SQLAlchemy URL like:
    #   postgresql+psycopg://user:pass@host:port/dbname
    # Strip the +psycopg driver qualifier.
    url = url.replace("postgresql+psycopg://", "postgresql://")
    env = os.environ.copy()
    if "@" in url:
        # Let pg_dump parse it from the connection string
        pass
    subprocess.run(
        ["pg_dump", "-d", url, "-f", str(dest), "--no-owner", "--no-acl"],
        check=True, env=env, capture_output=True, text=True,
    )
    # Compress
    with open(dest, "rb") as f_in:
        with gzip.open(str(dest) + ".gz", "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    dest.unlink()


def _prune_old_backups() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    for f in BACKUP_DIR.glob("backup-*"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                print(f"  Pruned old backup: {f.name}")
        except OSError:
            pass


def main() -> None:
    _load_env()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    url = _database_url()
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")

    if _is_sqlite(url):
        db_path = _sqlite_path(url)
        if not db_path.exists():
            print(f"Database not found at {db_path} — nothing to back up.")
            sys.exit(1)
        dest = BACKUP_DIR / f"backup-{stamp}.sqlite"
        print(f"Backing up SQLite: {db_path} → {dest}")
        _backup_sqlite(db_path, dest)
    else:
        dest = BACKUP_DIR / f"backup-{stamp}.sql"
        print(f"Backing up Postgres: {url} → {dest}.gz")
        _backup_postgres(url, dest)

    _prune_old_backups()
    print("Backup complete.")


if __name__ == "__main__":
    main()
