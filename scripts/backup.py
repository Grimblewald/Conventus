#!/usr/bin/env python3
"""Backup and restore script for Conventus.

Creates a zip archive containing the database and all uploaded files.
Old backups are pruned after KEEP_DAYS (default 30).

Usage:
  uv run python scripts/backup.py              # create backup
  uv run python scripts/backup.py --restore    # restore latest backup
  uv run python scripts/backup.py --restore backups/backup-2026-06-02-162751.zip
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTANCE_DIR = PROJECT_ROOT / "instance"
UPLOADS_DIR = PROJECT_ROOT / "uploads"
BACKUP_DIR = PROJECT_ROOT / "backups"
KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS", "30"))


def _load_env() -> None:
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
    prefix = "sqlite:///"
    path = url[len(prefix):]
    if path.startswith("/"):
        return Path(path)
    return PROJECT_ROOT / path


def _backup_sqlite_to_file(db_path: Path, dest: Path) -> None:
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    src.backup(dst)
    dst.close()
    src.close()


def _backup_postgres_to_file(url: str, dest: Path) -> None:
    from urllib.parse import unquote, urlparse

    url = url.replace("postgresql+psycopg://", "postgresql://")
    env = os.environ.copy()
    parsed = urlparse(url)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    subprocess.run(
        ["pg_dump", "-d", url, "-f", str(dest), "--no-owner", "--no-acl"],
        check=True, env=env, capture_output=True, text=True,
    )


def _create_backup() -> Path:
    _load_env()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    url = _database_url()
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    zip_path = BACKUP_DIR / f"backup-{stamp}.zip"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Back up database
        if _is_sqlite(url):
            db_path = _sqlite_path(url)
            if not db_path.exists():
                print(f"Database not found at {db_path} — nothing to back up.")
                sys.exit(1)
            db_dest = tmp_path / "app.db"
            print(f"  Database: {db_path}")
            _backup_sqlite_to_file(db_path, db_dest)
        else:
            db_dest = tmp_path / "app.sql"
            print(f"  Database (postgres): {url}")
            _backup_postgres_to_file(url, db_dest)

        # Copy instance flags
        for flag in [".setup-complete", "setup-pw"]:
            flag_path = INSTANCE_DIR / flag
            if flag_path.exists():
                shutil.copy2(flag_path, tmp_path / flag)

        # Bundle everything into zip
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Database
            zf.write(db_dest, f"instance/{db_dest.name}")

            # Instance flags
            for flag in [".setup-complete", "setup-pw"]:
                flag_path = tmp_path / flag
                if flag_path.exists():
                    zf.write(flag_path, f"instance/{flag}")

            # Uploads directory
            if UPLOADS_DIR.exists():
                file_count = 0
                for file in UPLOADS_DIR.rglob("*"):
                    if file.is_file():
                        zf.write(file, f"uploads/{file.relative_to(UPLOADS_DIR)}")
                        file_count += 1
                print(f"  Uploads: {file_count} files from {UPLOADS_DIR}")
            else:
                print("  Uploads: directory not found, skipping")

    _prune_old_backups()
    return zip_path


def _restore_backup(zip_path: Path) -> None:
    if not zip_path.exists():
        print(f"Backup file not found: {zip_path}")
        sys.exit(1)

    if not zipfile.is_zipfile(zip_path):
        print(f"Not a valid zip archive: {zip_path}")
        sys.exit(1)

    print(f"Restoring from: {zip_path}")
    print("This will overwrite the current database and uploads. Continue? [y/N] ", end="", flush=True)
    answer = input().strip().lower()
    if answer != "y":
        print("Restore cancelled.")
        sys.exit(0)

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()

        # Restore database
        db_files = [n for n in names if n.startswith("instance/") and not n.endswith("/")]
        for db_entry in db_files:
            dest = PROJECT_ROOT / db_entry
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(db_entry) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            print(f"  Restored: {dest.relative_to(PROJECT_ROOT)}")

        # Restore uploads — clear existing first
        upload_entries = [n for n in names if n.startswith("uploads/") and not n.endswith("/")]
        if upload_entries:
            if UPLOADS_DIR.exists():
                shutil.rmtree(UPLOADS_DIR)
                print(f"  Cleared existing uploads/")
            for entry in upload_entries:
                dest = PROJECT_ROOT / entry
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(entry) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            print(f"  Restored: {len(upload_entries)} uploaded files")
        else:
            print("  No uploads in this backup.")

    print("Restore complete. Restart the app to apply changes.")


def _find_latest_backup() -> Path | None:
    zips = sorted(BACKUP_DIR.glob("backup-*.zip"), reverse=True)
    return zips[0] if zips else None


def _prune_old_backups() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    for f in BACKUP_DIR.glob("backup-*.zip"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                print(f"  Pruned old backup: {f.name}")
        except OSError:
            pass


def main() -> None:
    args = sys.argv[1:]

    if not args:
        print("Creating backup...")
        zip_path = _create_backup()
        print(f"Backup complete: {zip_path.relative_to(PROJECT_ROOT)}")
        return

    if args[0] == "--restore":
        _load_env()
        if len(args) >= 2:
            zip_path = Path(args[1])
            if not zip_path.is_absolute():
                zip_path = PROJECT_ROOT / zip_path
        else:
            zip_path = _find_latest_backup()
            if zip_path is None:
                print(f"No backups found in {BACKUP_DIR}")
                sys.exit(1)
            print(f"Latest backup: {zip_path.name}")
        _restore_backup(zip_path)
        return

    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    main()
