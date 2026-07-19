#!/usr/bin/env python3
"""Backup and restore CLI for Conventus.

Thin wrapper over ``app.services.backup_archive`` — the same archive
format the admin Backup panel produces, so zips restore interchangeably.
Old backups are pruned after KEEP_DAYS (default 30).

This CLI never includes .env in a backup; password-protected full
backups are only available through the admin panel (OTP-gated).

Usage:
  uv run python scripts/backup.py              # create backup
  uv run python scripts/backup.py --restore    # restore latest backup
  uv run python scripts/backup.py --restore backups/backup-2026-06-02-162751.zip
"""
from __future__ import annotations

import getpass
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = PROJECT_ROOT / "backups"
KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS", "30"))

sys.path.insert(0, str(PROJECT_ROOT))


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


def _app_context():
    from app import create_app
    app = create_app()
    return app.app_context()


def _create_backup() -> None:
    from app.services.backup_archive import build_backup_zip

    print("Creating backup...")
    with _app_context():
        zip_path = build_backup_zip(BACKUP_DIR)
    _prune_old_backups()
    print(f"Backup complete: {zip_path.relative_to(PROJECT_ROOT)}")


def _restore_backup(zip_path: Path) -> None:
    from app.services.backup_archive import (
        is_encrypted_zip, restore_backup_zip, validate_backup_zip,
    )

    if not zip_path.exists():
        print(f"Backup file not found: {zip_path}")
        sys.exit(1)

    err = validate_backup_zip(zip_path)
    if err:
        print(f"Invalid backup: {err}")
        sys.exit(1)

    print(f"Restoring from: {zip_path}")
    print("This will overwrite the current database and uploads. Continue? [y/N] ",
          end="", flush=True)
    if input().strip().lower() != "y":
        print("Restore cancelled.")
        sys.exit(0)

    password = None
    if is_encrypted_zip(zip_path):
        password = getpass.getpass("Backup password: ")

    with _app_context():
        try:
            warnings = restore_backup_zip(zip_path, password=password)
        except RuntimeError as e:
            if "password" in str(e).lower():
                print("Restore failed: wrong backup password.")
            else:
                print(f"Restore failed: {e}")
            sys.exit(1)

    for w in warnings:
        print(f"  Note: {w}")
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
    _load_env()

    if not args:
        _create_backup()
        return

    if args[0] == "--restore":
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
