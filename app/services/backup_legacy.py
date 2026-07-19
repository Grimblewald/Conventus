"""Restore support for legacy (pre-manifest, v1 CLI-layout) backup zips.

Legacy archives store the database at ``instance/app.db`` instead of the
canonical root-level ``app.db`` and carry no manifest. This module exists
solely so those old zips remain restorable.

DELETE THIS MODULE once backups created before 2026-07 are out of
circulation — the only import is the conditional in
``backup_archive.restore_backup_zip()``.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def is_legacy_zip(names: set[str]) -> bool:
    """Legacy layout: database under instance/, nothing at the root."""
    return ("instance/app.db" in names
            and "app.db" not in names and "app.sql" not in names)


def restore_legacy_zip(zf, *, sqlite_path: Path, uploads_root: Path,
                       instance_dir: Path) -> list[str]:
    """Restore a legacy-layout archive. Returns human-readable warnings."""
    import shutil

    warnings = ["legacy backup layout (pre-2026-07) — consider re-creating "
                "this backup in the current format after restoring"]
    names = set(zf.namelist())

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_path.write_bytes(zf.read("instance/app.db"))

    for entry in names:
        if (entry.startswith("instance/") and entry != "instance/app.db"
                and not entry.endswith("/")):
            dest = instance_dir / Path(entry).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(entry))

    upload_entries = [n for n in names
                      if n.startswith("uploads/") and not n.endswith("/")]
    if upload_entries:
        if uploads_root.exists():
            shutil.rmtree(uploads_root)
        uploads_root.mkdir(parents=True, exist_ok=True)
        for entry in upload_entries:
            dest = uploads_root / Path(entry).relative_to("uploads")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(entry))

    log.info("Restored legacy backup: db + %d uploads", len(upload_entries))
    return warnings
