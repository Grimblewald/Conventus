"""Canonical backup archive format — build, validate, restore.

One format shared by the admin Backup panel and ``scripts/backup.py``.

Layout (format 2):
  app.db | app.sql          database (SQLite copy or pg_dump)
  manifest.json             format, timestamps, git commit, migration head
  migration_head.txt        alembic revision at backup time
  instance/.setup-complete  setup flag
  instance/setup-pw         setup password file
  uploads/...               every uploaded file
  env/.env                  ONLY in password-protected full backups

Archives with a password are AES-256 encrypted (pyzipper); extract with
7-Zip/p7zip — Windows Explorer's built-in extractor cannot open them.

Legacy (pre-manifest) archives are handled by ``backup_legacy`` — a
separate module so it can be deleted outright when those zips retire.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from flask import current_app

log = logging.getLogger(__name__)

FORMAT_VERSION = 2
MIN_ENV_PASSWORD_LEN = 12


# ---------------------------------------------------------------------------
# Path / database helpers
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    return Path(current_app.root_path).parent


def database_url() -> str:
    raw = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    return (os.environ.get("DATABASE_URL") or raw or
            f"sqlite:///{current_app.instance_path}/app.db")


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def sqlite_path(url: str) -> Path:
    path = url[len("sqlite:///"):]
    if path.startswith("/"):
        return Path(path)
    return _project_root() / path


def _sqlite_copy(src: Path, dst: Path) -> None:
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    s.backup(d)
    d.close()
    s.close()


def _pg_env(url: str) -> tuple[str, dict]:
    from urllib.parse import unquote, urlparse
    url = url.replace("postgresql+psycopg://", "postgresql://")
    env = os.environ.copy()
    parsed = urlparse(url)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    return url, env


def _pg_dump(url: str, dest: Path) -> None:
    url, env = _pg_env(url)
    subprocess.run(["pg_dump", "-d", url, "-f", str(dest), "--no-owner", "--no-acl"],
                   check=True, env=env, capture_output=True, text=True)


def _pg_restore(dump_path: Path, url: str) -> None:
    url, env = _pg_env(url)
    subprocess.run(["psql", "-d", url, "-f", str(dump_path)],
                   check=True, env=env, capture_output=True, text=True)


def _current_migration_head() -> str:
    try:
        from sqlalchemy import text
        from ..extensions import db
        row = db.session.execute(text("SELECT version_num FROM alembic_version")).first()
        return row[0] if row else "unknown"
    except Exception:
        return "unknown"


def _git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                      cwd=str(_project_root()), timeout=10)
        return out.decode().strip()
    except Exception:
        return "unknown"


def _known_migration(revision: str) -> bool:
    """True if *revision* exists in this checkout's migrations directory."""
    versions = _project_root() / "migrations" / "versions"
    try:
        return any(revision in f.read_text() for f in versions.glob("*.py"))
    except Exception:
        return True  # can't tell — don't block the restore on it


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_backup_zip(backup_dir: Path, *, include_env: bool = False,
                     password: str | None = None) -> Path:
    """Build a backup archive in *backup_dir* and return its path.

    ``include_env`` requires a ``password`` (the archive is then AES-256
    encrypted); scheduled/automatic callers must never set it.
    """
    if include_env and not password:
        raise ValueError(".env may only be included in a password-protected backup")
    if password and len(password) < MIN_ENV_PASSWORD_LEN:
        raise ValueError(f"Backup password must be at least {MIN_ENV_PASSWORD_LEN} characters")

    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])
    instance_dir = Path(current_app.instance_path)
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.utcnow().strftime("%Y-%m-%d-%H%M%S")
    name = f"backup-full-{stamp}.zip" if include_env else f"backup-{stamp}.zip"
    zip_path = backup_dir / name
    url = database_url()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        if is_sqlite(url):
            db_src = sqlite_path(url)
            if not db_src.exists():
                raise RuntimeError(f"Database not found at {db_src}")
            db_dest = tmp_path / "app.db"
            _sqlite_copy(db_src, db_dest)
        else:
            db_dest = tmp_path / "app.sql"
            _pg_dump(url, db_dest)

        head = _current_migration_head()
        manifest = {
            "format": FORMAT_VERSION,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "site_name": _site_name(),
            "git_commit": _git_commit(),
            "migration_head": head,
            "includes_env": include_env,
            "counts": _row_counts(uploads_root),
        }

        with _open_zip_for_write(zip_path, password) as zf:
            zf.write(db_dest, db_dest.name)
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            zf.writestr("migration_head.txt", head)

            for flag in (".setup-complete", "setup-pw"):
                flag_path = instance_dir / flag
                if flag_path.exists():
                    zf.write(flag_path, f"instance/{flag}")

            if uploads_root.exists():
                for f in sorted(uploads_root.rglob("*")):
                    if f.is_file():
                        zf.write(f, f"uploads/{f.relative_to(uploads_root)}")

            if include_env:
                env_path = _project_root() / ".env"
                if not env_path.exists():
                    raise RuntimeError(f"No .env found at {env_path}")
                zf.write(env_path, "env/.env")

    return zip_path


def _open_zip_for_write(zip_path: Path, password: str | None):
    if password:
        import pyzipper
        zf = pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES)
        zf.setpassword(password.encode())
        return zf
    return zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED)


def _site_name() -> str:
    try:
        from ..models.content import get_site_settings
        return get_site_settings().site_name
    except Exception:
        return "unknown"


def _row_counts(uploads_root: Path) -> dict:
    counts = {}
    try:
        from ..models import Registration, User
        counts["users"] = User.query.count()
        counts["registrations"] = Registration.query.count()
    except Exception:
        pass
    try:
        counts["uploads"] = sum(1 for f in uploads_root.rglob("*") if f.is_file())
    except Exception:
        pass
    return counts


# ---------------------------------------------------------------------------
# Validate / inspect
# ---------------------------------------------------------------------------

def is_encrypted_zip(zip_path: Path) -> bool:
    """True when any archive entry is encrypted (names stay readable)."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            return any(info.flag_bits & 0x1 for info in zf.infolist())
    except Exception:
        return False


def validate_backup_zip(zip_path: Path) -> str | None:
    """Return an error string, or None when the archive looks restorable."""
    if not zipfile.is_zipfile(zip_path):
        return "Not a valid zip archive."
    from .backup_legacy import is_legacy_zip
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
    if any(n in ("app.db", "app.sql") for n in names) or is_legacy_zip(names):
        return None
    return "Backup does not contain a database file (app.db or app.sql)."


def read_manifest(zip_path: Path, password: str | None = None) -> dict:
    """Best-effort manifest read (empty dict for legacy archives)."""
    try:
        with _open_zip_for_read(zip_path, password) as zf:
            if "manifest.json" in zf.namelist():
                return json.loads(zf.read("manifest.json"))
    except Exception:
        pass
    return {}


def _open_zip_for_read(zip_path: Path, password: str | None):
    import pyzipper
    zf = pyzipper.AESZipFile(zip_path, "r")
    if password:
        zf.setpassword(password.encode())
    return zf


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def _safety_snapshot() -> Path | None:
    """Copy the current SQLite database aside before a restore overwrites it, so
    a restore is reversible and can never irrecoverably destroy live data.

    Returns the snapshot path, or None when there is nothing to snapshot or the
    database is not SQLite (Postgres restores are out of scope here — the
    warning in `restore_backup_zip` tells the operator to dump first).
    """
    url = database_url()
    if not is_sqlite(url):
        return None
    src = sqlite_path(url)
    if not src.exists():
        return None
    from datetime import timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest_dir = _project_root() / "var" / "backups" / f"{stamp}-pre-restore"
    n = 1
    while dest_dir.exists():
        dest_dir = dest_dir.with_name(f"{stamp}-pre-restore-{n}")
        n += 1
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Use the SQLite backup API (same as build_backup_zip), not a raw file
    # copy: the app may still be writing, and copy2 would miss an active WAL,
    # producing a torn snapshot — defeating the point of a safety copy.
    _sqlite_copy(src, dest_dir / "app.db")
    return dest_dir


def restore_backup_zip(zip_path: Path, password: str | None = None) -> list[str]:
    """Restore an archive over the current instance. Returns warnings.

    Raises on failure (including a wrong password for encrypted zips). The
    current database is copied aside first (see `_safety_snapshot`) so the
    restore can be undone.
    """
    url = database_url()
    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])
    instance_dir = Path(current_app.instance_path)
    warnings: list[str] = []

    safety = _safety_snapshot()
    if safety is not None:
        rel = safety.relative_to(_project_root())
        warnings.append(f"current database copied to {rel} before restore")
    elif not is_sqlite(url):
        warnings.append("no pre-restore snapshot taken (non-SQLite database) — "
                        "dump it yourself before trusting this restore")

    with _open_zip_for_read(zip_path, password) as zf:
        names = set(zf.namelist())

        from .backup_legacy import is_legacy_zip
        if is_legacy_zip(names):
            from .backup_legacy import restore_legacy_zip
            if not is_sqlite(url):
                raise RuntimeError("Legacy backups only support SQLite databases.")
            warnings += restore_legacy_zip(
                zf, sqlite_path=sqlite_path(url),
                uploads_root=uploads_root, instance_dir=instance_dir)
        else:
            warnings += _restore_v2(zf, names, url, uploads_root, instance_dir)

    _run_migrations()
    _clear_booklet_cache(uploads_root)
    return warnings


def _restore_v2(zf, names: set[str], url: str,
                uploads_root: Path, instance_dir: Path) -> list[str]:
    warnings: list[str] = []

    if "manifest.json" in names:
        try:
            manifest = json.loads(zf.read("manifest.json"))
            head = manifest.get("migration_head", "")
            if head and head != "unknown" and not _known_migration(head):
                warnings.append(
                    f"backup was created on newer code (migration {head} is "
                    f"unknown to this checkout) — update the code before "
                    f"trusting this restore")
        except Exception:
            warnings.append("manifest.json unreadable — continuing anyway")

    if "app.db" in names and is_sqlite(url):
        db_dest = sqlite_path(url)
        db_dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(zf.read("app.db"))
        shutil.move(tmp.name, str(db_dest))
    elif "app.sql" in names and not is_sqlite(url):
        with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as tmp:
            tmp.write(zf.read("app.sql"))
        _pg_restore(Path(tmp.name), url)
        Path(tmp.name).unlink(missing_ok=True)
    else:
        raise RuntimeError("Backup database type does not match the configured database.")

    upload_entries = [n for n in names
                      if n.startswith("uploads/") and not n.endswith("/")]
    if uploads_root.exists():
        shutil.rmtree(uploads_root)
    uploads_root.mkdir(parents=True, exist_ok=True)
    for entry in upload_entries:
        dest = uploads_root / Path(entry).relative_to("uploads")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(entry))

    for flag in (".setup-complete", "setup-pw"):
        entry = f"instance/{flag}"
        if entry in names:
            dest = instance_dir / flag
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(entry))

    if "env/.env" in names:
        env_dest = _project_root() / ".env"
        if env_dest.exists():
            warnings.append("archive contains .env; the existing .env was "
                            "left untouched")
        else:
            env_dest.write_bytes(zf.read("env/.env"))
            warnings.append(".env restored from the archive")

    return warnings


def _run_migrations() -> None:
    subprocess.run(
        ["uv", "run", "flask", "--app", "wsgi:app", "db", "upgrade"],
        cwd=str(_project_root()), check=True, capture_output=True, text=True,
    )


def _clear_booklet_cache(uploads_root: Path) -> None:
    cache_dir = uploads_root / "abstracts" / ".booklet-cache"
    if cache_dir.exists():
        for f in cache_dir.glob("*.zip"):
            f.unlink(missing_ok=True)
