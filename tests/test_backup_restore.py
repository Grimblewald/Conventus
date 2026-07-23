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


def test_invalid_archive_is_rejected(isolated_app, tmp_path):
    import zipfile
    from app.services.backup_archive import validate_backup_zip

    bad = tmp_path / "not-a-backup.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("random.txt", "nope")
    assert validate_backup_zip(bad) is not None
