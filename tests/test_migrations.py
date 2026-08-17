"""The migration chain must reach head on the databases we actually ship onto.

Two starting states matter, and only one of them is the textbook one:

  * a genuinely empty database — a fresh install;
  * a database whose tables were created by `db.create_all()` at boot and then
    stamped with the baseline revision. This is not hypothetical: the app
    factory calls create_all, so any database first opened by a newer build
    already carries every model column, and `scripts/update.sh` explicitly
    stamps the baseline and upgrades when it finds no alembic_version — the
    path a restore from a raw database backup takes.

The second state is what broke: several migrations guarded their add_column
with a try/except *inside* `with op.batch_alter_table(...)`. Batch operations
are recorded and only emitted when the context manager exits, so the except
clause never ran and the upgrade died on "duplicate column name". A fresh
install and a raw-backup restore could not reach head.

These drive the real `flask db` CLI against throwaway databases, so the whole
chain is exercised end to end, including migrations added later.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# The revision scripts/update.sh stamps when alembic_version is missing.
BASELINE = "4a1b2c3d4e5f"


def _run(args, db_path, **kw):
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_path}",
        "FLASK_APP": "wsgi:app",
        # Never let a developer's .env or a stale instance path leak in.
        "SECRET_KEY": "test-secret-for-migrations",
    }
    return subprocess.run(args, cwd=PROJECT_ROOT, env=env,
                          capture_output=True, text=True, **kw)


def _flask_db(db_path, *args):
    return _run([sys.executable, "-m", "flask", "db", *args], db_path)


def _table_columns(db_path, table):
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _current_revision(db_path):
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        return rows[0][0] if rows else None
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "app.db"


def test_fresh_database_upgrades_to_head(db_path):
    """A brand-new install runs the whole chain from nothing."""
    r = _flask_db(db_path, "upgrade")
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert db_path.exists()
    assert _current_revision(db_path)
    # Spot-check columns from migrations across the chain, including the two
    # newest (financial identity) and the batch-guarded ones.
    assert "hero_image_mode" in _table_columns(db_path, "conferences")
    assert "booklet_header_filename" in _table_columns(db_path, "conferences")
    assert "is_contactable" in _table_columns(db_path, "committee_members")
    assert "gst_registered" in _table_columns(db_path, "financial_identity")
    assert "speaker_bio" in _table_columns(db_path, "abstracts")


def test_create_all_bootstrapped_database_upgrades_from_the_baseline(db_path):
    """The regression: create_all first, stamp the baseline, then upgrade.

    Every column the later migrations add is ALREADY present, so each of them
    must detect that and skip rather than re-adding it.
    """
    # Build the schema the way the app factory does at boot.
    boot = _run([sys.executable, "-c",
                 "from app import create_app\n"
                 "from app.extensions import db\n"
                 "app = create_app()\n"
                 "with app.app_context():\n"
                 "    import app.models  # noqa: F401\n"
                 "    db.create_all()\n"], db_path)
    assert boot.returncode == 0, f"stdout={boot.stdout}\nstderr={boot.stderr}"
    assert "hero_image_mode" in _table_columns(db_path, "conferences")

    stamp = _flask_db(db_path, "stamp", BASELINE)
    assert stamp.returncode == 0, f"stdout={stamp.stdout}\nstderr={stamp.stderr}"
    assert _current_revision(db_path) == BASELINE

    up = _flask_db(db_path, "upgrade")
    assert up.returncode == 0, (
        "upgrade over a create_all-bootstrapped database failed — a fresh "
        f"install or raw-backup restore cannot reach head.\n"
        f"stdout={up.stdout}\nstderr={up.stderr}")
    assert _current_revision(db_path) != BASELINE

    # And it really did reach the newest revisions, not just survive.
    assert "gst_registered" in _table_columns(db_path, "financial_identity")


def test_no_migration_guards_ddl_inside_a_batch_context(tmp_path):
    """Static guard against the pattern coming back.

    `try:` inside `with op.batch_alter_table(...)` is always wrong — the DDL is
    emitted at context exit, outside the except clause's reach. Check for the
    existence of the anti-pattern rather than relying on someone re-running the
    upgrade test after adding a migration.
    """
    offenders = []
    for path in sorted((PROJECT_ROOT / "migrations" / "versions").glob("*.py")):
        in_batch = False
        indent = 0
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            cur_indent = len(line) - len(line.lstrip())
            if in_batch and cur_indent <= indent:
                in_batch = False
            if "batch_alter_table" in stripped and stripped.startswith("with "):
                in_batch = True
                indent = cur_indent
                continue
            if in_batch and stripped.startswith(("try:", "except ")):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "try/except inside a batch_alter_table block cannot catch the DDL "
        "error — the operations are emitted when the block exits. Use an "
        "sa.inspect() existence check before opening the batch. Offenders: "
        + ", ".join(offenders))
