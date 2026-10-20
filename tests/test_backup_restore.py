"""Backup archive round-trip and restore-safety.

Data loss must never be a side effect of a restore. A restore is an explicitly
destructive action, but the current database is copied aside first so it can be
recovered if the wrong archive was chosen or the restore is regretted.

These run against a fully isolated app (own temp DB, uploads and project root)
so a real restore — which overwrites the database and wipes uploads — cannot
touch the shared test database.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    from app.config import DevelopmentConfig

    inst = tmp_path / "instance"
    inst.mkdir()
    db_path = inst / "app.db"

    class Cfg(DevelopmentConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
        UPLOAD_FOLDER = str(tmp_path / "uploads")
        FINANCIAL_ASSETS_DIR = str(tmp_path / "financial-assets")
        TESTING = True
        WTF_CSRF_ENABLED = False

    from app import create_app
    app = create_app(Cfg)
    app.instance_path = str(inst)

    # Keep the archive machinery inside tmp: snapshots and any relative paths
    # resolve under here, not the real repo.
    monkeypatch.setattr("app.services.backup_archive._project_root",
                        lambda: tmp_path)
    # A real restore shells out `flask db upgrade`; not under test here.
    monkeypatch.setattr("app.services.backup_archive._run_migrations",
                        lambda: None)

    with app.app_context():
        from app.extensions import db
        db.create_all()
        Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
        yield app


def _sqlite_file(app):
    from app.services.backup_archive import database_url, sqlite_path
    return sqlite_path(database_url())


def test_backup_round_trips(isolated_app, tmp_path):
    from app.services.backup_archive import (
        build_backup_zip, restore_backup_zip, validate_backup_zip,
    )
    with isolated_app.app_context():
        zip_path = build_backup_zip(tmp_path / "out")
        assert zip_path.exists()
        assert validate_backup_zip(zip_path) is None
        warnings = restore_backup_zip(zip_path)
        assert any("before restore" in w for w in warnings)


def test_restore_snapshots_the_current_db_first(isolated_app, tmp_path):
    """The heart of the guarantee: the live DB is copied aside before the
    archive overwrites it, so a restore is reversible. The snapshot is taken
    with the SQLite backup API, so it captures committed database content —
    verified here via a distinctive user_version, not raw trailing bytes."""
    import sqlite3

    from app.services.backup_archive import build_backup_zip, restore_backup_zip

    with isolated_app.app_context():
        archive = build_backup_zip(tmp_path / "out")

        # Mark the CURRENT database with a committed change (a real DB write),
        # so we can prove the pre-restore snapshot captured *this* state.
        marker = 8675309
        live = _sqlite_file(isolated_app)
        conn = sqlite3.connect(str(live))
        conn.execute(f"PRAGMA user_version = {marker}")
        conn.commit()
        conn.close()

        restore_backup_zip(archive)

    snaps = list((tmp_path / "var" / "backups").glob("*-pre-restore*/app.db"))
    assert snaps, "no pre-restore snapshot was written"
    versions = []
    for p in snaps:
        c = sqlite3.connect(str(p))
        versions.append(c.execute("PRAGMA user_version").fetchone()[0])
        c.close()
    assert marker in versions


def test_restore_survives_pending_wal_frames(isolated_app, tmp_path):
    """The live database runs in WAL mode. Frames still sitting in its -wal
    file when the restore lands must not be replayed over the archive's
    database — that silently undid a restore of real data."""
    import sqlite3

    from app.services.backup_archive import build_backup_zip, restore_backup_zip

    with isolated_app.app_context():
        live = _sqlite_file(isolated_app)

        conn = sqlite3.connect(str(live))
        conn.execute("PRAGMA user_version = 111")
        conn.commit()
        conn.close()
        archive = build_backup_zip(tmp_path / "out")

        # Held open with checkpointing off, so the change stays in the WAL
        # rather than being folded into the main file.
        held = sqlite3.connect(str(live))
        held.execute("PRAGMA journal_mode = wal")
        held.execute("PRAGMA wal_autocheckpoint = 0")
        held.execute("PRAGMA user_version = 222")
        held.commit()
        assert Path(f"{live}-wal").stat().st_size > 0

        restore_backup_zip(archive)

        check = sqlite3.connect(str(live))
        restored = check.execute("PRAGMA user_version").fetchone()[0]
        check.close()
        held.close()

    assert restored == 111


def test_document_logo_and_signature_round_trip(isolated_app, tmp_path):
    """They live outside uploads so they are never web-served; a backup that
    left them behind restored a site whose documents had neither."""
    from app.services.backup_archive import build_backup_zip, restore_backup_zip

    assets = Path(isolated_app.config["FINANCIAL_ASSETS_DIR"])
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "logo.png").write_bytes(b"logo-bytes")
    (assets / "signature.png").write_bytes(b"signature-bytes")

    with isolated_app.app_context():
        archive = build_backup_zip(tmp_path / "out")
        (assets / "logo.png").unlink()
        (assets / "signature.png").write_bytes(b"replaced since")
        restore_backup_zip(archive)

    assert (assets / "logo.png").read_bytes() == b"logo-bytes"
    assert (assets / "signature.png").read_bytes() == b"signature-bytes"


def test_an_archive_without_assets_leaves_them_alone(isolated_app, tmp_path):
    """Backups made before assets were included must not wipe the current ones."""
    import zipfile

    from app.services.backup_archive import build_backup_zip, restore_backup_zip

    assets = Path(isolated_app.config["FINANCIAL_ASSETS_DIR"])
    with isolated_app.app_context():
        archive = build_backup_zip(tmp_path / "out")
    older = tmp_path / "older.zip"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(older, "w") as dst:
        for item in src.infolist():
            if not item.filename.startswith("financial-assets/"):
                dst.writestr(item, src.read(item))

    assets.mkdir(parents=True, exist_ok=True)
    (assets / "logo.png").write_bytes(b"current logo")
    with isolated_app.app_context():
        restore_backup_zip(older)
    assert (assets / "logo.png").read_bytes() == b"current logo"


def test_invalid_archive_is_rejected(isolated_app, tmp_path):
    import zipfile
    from app.services.backup_archive import validate_backup_zip

    bad = tmp_path / "not-a-backup.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("random.txt", "nope")
    assert validate_backup_zip(bad) is not None
