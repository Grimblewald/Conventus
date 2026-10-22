"""Data-safety guarantees for updating and reverting the site.

Regression cover for the 2026-07-22 incident: reverting a broken deploy lost
data. The old script drove a single backup slot (`db.bak`) overwritten on
every run, so a second update during an incident destroyed the good snapshot,
and `--revert` overwrote the live database with no safety copy.

Also for what replaced it: a revert copied the snapshot over a live SQLite
database without stopping the site, leaving its write-ahead log beside it to be
replayed over the restored data; and a snapshot taken with `cp` could miss
writes still in that log.

These drive the REAL scripts/update.sh (and through it scripts/site_update.py)
against a throwaway git repo with a real SQLite database. git pull, uv,
systemctl and fuser are stand-ins that record what they were asked to do.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _write_db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path))
    c.execute("create table if not exists t (v text)")
    c.execute("delete from t")
    c.execute("insert into t values (?)", (value,))
    c.commit()
    c.close()


def _read_db(path: Path) -> str:
    c = sqlite3.connect(str(path))
    try:
        return c.execute("select v from t").fetchone()[0]
    finally:
        c.close()


@pytest.fixture
def repo(tmp_path):
    """A git repo laid out like the project: instance/app.db plus the scripts."""
    root = tmp_path / "site"
    (root / "scripts").mkdir(parents=True)
    for name in ("update.sh", "site_update.py"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
    os.chmod(root / "scripts" / "update.sh", 0o755)
    _write_db(root / "instance" / "app.db", "data-v1")
    (root / ".gitignore").write_text("instance/\nvar/\n")

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.org")
    _git(root, "config", "user.name", "Test")
    (root / "README").write_text("v1")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "v1")
    return root


@pytest.fixture
def shims(tmp_path):
    """Stand-ins that record their calls. Returns (PATH, call log)."""
    bindir = tmp_path / "shim-bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    (bindir / "git").write_text(
        '#!/usr/bin/env bash\n'
        f'if [ "$1" = "pull" ]; then echo "git pull" >> {log}; exit "${{PULL_EXIT:-0}}"; fi\n'
        'exec /usr/bin/git "$@"\n')
    (bindir / "systemctl").write_text(
        '#!/usr/bin/env bash\n'
        f'echo "systemctl $*" >> {log}\n'
        'if [ "$2" = "is-active" ]; then echo active; fi\n')
    (bindir / "fuser").write_text(f'#!/usr/bin/env bash\necho "fuser $*" >> {log}\n')
    (bindir / "uv").write_text(
        '#!/usr/bin/env bash\n'
        f'echo "uv $*" >> {log}\n'
        'for a in "$@"; do if [ "$a" = "current" ]; then echo "4a1b2c3d4e5f (head)"; exit 0; fi; done\n'
        'for a in "$@"; do if [ "$a" = "upgrade" ]; then exit "${MIGRATE_EXIT:-0}"; fi; done\n'
        'exit 0\n')
    for f in bindir.iterdir():
        os.chmod(f, 0o755)
    return f"{bindir}:{os.environ['PATH']}", log


def _run(repo, shims, *args, stdin=None, **env):
    path, _log = shims
    return subprocess.run(
        ["bash", str(repo / "scripts" / "update.sh"), *args],
        cwd=repo, capture_output=True, text=True, stdin=stdin,
        env={**os.environ, "PATH": path, "HOME": str(repo), **env})


def _calls(shims) -> list[str]:
    log = shims[1]
    return log.read_text().splitlines() if log.exists() else []


def _backup_dirs(repo):
    d = repo / "var" / "backups"
    return sorted(p for p in d.iterdir() if p.is_dir()) if d.exists() else []


# --- updating -----------------------------------------------------------------

def test_update_writes_a_restore_point_then_pulls_migrates_restarts(repo, shims):
    r = _run(repo, shims)
    assert r.returncode == 0, r.stdout + r.stderr
    snaps = _backup_dirs(repo)
    assert len(snaps) == 1
    assert _read_db(snaps[0] / "app.db") == "data-v1"
    assert (snaps[0] / "git-head").read_text().strip() == _git(repo, "rev-parse", "HEAD")
    calls = _calls(shims)
    steps = [c.split()[0] + (" " + c.split()[1] if c.startswith("uv") else "")
             for c in calls]
    assert steps.index("git") < steps.index("uv sync") < steps.index("uv run")
    assert any("systemctl --user restart cloudflared-launch.service" == c for c in calls)


def test_a_restore_point_includes_writes_still_in_the_write_ahead_log(repo, shims):
    """A plain file copy of a live WAL-mode database misses these."""
    db = repo / "instance" / "app.db"
    held = sqlite3.connect(str(db))
    held.execute("pragma journal_mode=wal")
    held.execute("pragma wal_autocheckpoint=0")
    held.execute("update t set v='written-moments-ago'")
    held.commit()
    assert Path(f"{db}-wal").stat().st_size > 0
    try:
        assert _run(repo, shims).returncode == 0
    finally:
        held.close()
    assert _read_db(_backup_dirs(repo)[0] / "app.db") == "written-moments-ago"


def test_a_failed_migration_stops_before_the_restart(repo, shims):
    r = _run(repo, shims, MIGRATE_EXIT="1")
    assert r.returncode != 0
    assert len(_backup_dirs(repo)) == 1, "the restore point is still written first"
    assert not [c for c in _calls(shims) if "restart" in c]


def test_a_failed_pull_changes_nothing_but_the_restore_point(repo, shims):
    r = _run(repo, shims, PULL_EXIT="1")
    assert r.returncode != 0
    assert not [c for c in _calls(shims) if c.startswith(("uv", "systemctl"))]


def test_second_update_does_not_clobber_the_first_snapshot(repo, shims):
    """The core of the incident: a second update must NOT overwrite the good
    restore point taken by the first."""
    _run(repo, shims)
    _write_db(repo / "instance" / "app.db", "data-v2-with-live-writes")
    _run(repo, shims)
    contents = sorted(_read_db(s / "app.db") for s in _backup_dirs(repo))
    assert contents == ["data-v1", "data-v2-with-live-writes"]


def test_old_snapshots_are_pruned_but_safety_copies_kept(repo, shims):
    backups = repo / "var" / "backups"
    keeper = backups / "20260101-090000-pre-restore"
    keeper.mkdir(parents=True)
    _write_db(keeper / "app.db", "PRE-RESTORE-LIVE-DB")
    for i in range(3):
        _write_db(repo / "instance" / "app.db", f"data-{i}")
        _run(repo, shims, UPDATE_KEEP_SNAPSHOTS="2")
    update_snaps = [s for s in _backup_dirs(repo) if "pre-" not in s.name]
    assert len(update_snaps) == 2, [s.name for s in update_snaps]
    assert _read_db(keeper / "app.db") == "PRE-RESTORE-LIVE-DB"


# --- reverting ----------------------------------------------------------------

def test_revert_puts_back_database_and_code_and_keeps_what_it_replaced(repo, shims):
    _run(repo, shims)
    good = _git(repo, "rev-parse", "HEAD")
    (repo / "README").write_text("v2")
    _git(repo, "commit", "-qam", "v2")
    _write_db(repo / "instance" / "app.db", "data-v2-live")

    r = _run(repo, shims, "--revert", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _read_db(repo / "instance" / "app.db") == "data-v1"
    assert _git(repo, "rev-parse", "HEAD") == good
    pre = [s for s in _backup_dirs(repo) if s.name.endswith("pre-revert")]
    assert pre and _read_db(pre[0] / "app.db") == "data-v2-live"


def test_revert_stops_the_site_before_touching_the_database(repo, shims):
    _run(repo, shims)
    shims[1].unlink()
    _run(repo, shims, "--revert", "--yes")
    calls = _calls(shims)
    stop = calls.index("systemctl --user stop cloudflared-launch.service")
    start = calls.index("systemctl --user start cloudflared-launch.service")
    assert stop < start


def test_revert_survives_a_write_ahead_log_left_beside_the_database(repo, shims):
    """The old revert copied the file over a live database; its leftover -wal
    was then replayed over the restored data."""
    _run(repo, shims)
    db = repo / "instance" / "app.db"
    held = sqlite3.connect(str(db))
    held.execute("pragma journal_mode=wal")
    held.execute("pragma wal_autocheckpoint=0")
    held.execute("update t set v='after-the-update'")
    held.commit()
    try:
        r = _run(repo, shims, "--revert", "--yes")
    finally:
        held.close()
    assert r.returncode == 0, r.stdout + r.stderr
    assert _read_db(db) == "data-v1"


def test_revert_works_when_the_app_cannot_even_import(repo, shims):
    """The point of keeping the scripts free of the app."""
    _run(repo, shims)
    (repo / "app").mkdir()
    (repo / "app" / "__init__.py").write_text("this is not python(\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "broken")
    r = _run(repo, shims, "--revert", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (repo / "app").exists()


def test_revert_without_yes_makes_no_change(repo, shims):
    _run(repo, shims)
    _write_db(repo / "instance" / "app.db", "data-v2-live")
    r = _run(repo, shims, "--revert", stdin=subprocess.DEVNULL)
    assert r.returncode != 0
    assert _read_db(repo / "instance" / "app.db") == "data-v2-live"


def test_revert_with_no_snapshot_errors_cleanly(repo, shims):
    r = _run(repo, shims, "--revert", "--yes")
    assert r.returncode != 0
    assert "nothing to revert" in (r.stdout + r.stderr).lower()
    assert _read_db(repo / "instance" / "app.db") == "data-v1"


def test_revert_reports_when_snapshots_hold_no_database(repo, shims):
    for name in ("20260101-100000", "20260102-100000"):
        d = repo / "var" / "backups" / name
        d.mkdir(parents=True)
        (d / "git-head").write_text("deadbeef")
    r = _run(repo, shims, "--revert", "--yes")
    assert r.returncode != 0
    assert "nothing to revert" in (r.stdout + r.stderr).lower()


def test_revert_never_restores_from_a_safety_copy(repo, shims):
    backups = repo / "var" / "backups"
    upd = backups / "20260101-100000"
    upd.mkdir(parents=True)
    _write_db(upd / "app.db", "UPDATE-SNAPSHOT")
    (upd / "git-head").write_text("")
    for name in ("20260201-120000-pre-revert", "20260201-120000-pre-revert-1",
                 "20260301-120000-pre-restore", "20260301-120000-pre-restore-1"):
        _write_db(backups / name / "app.db", "NOT-A-ROLLBACK-TARGET")
    _write_db(repo / "instance" / "app.db", "CURRENT-LIVE")
    r = _run(repo, shims, "--revert", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _read_db(repo / "instance" / "app.db") == "UPDATE-SNAPSHOT"


def test_revert_falls_back_to_a_legacy_single_slot_backup(repo, shims):
    _write_db(repo / "var" / "backups" / "db.bak", "LEGACY-BACKUP")
    _write_db(repo / "instance" / "app.db", "CURRENT-LIVE")
    r = _run(repo, shims, "--revert", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _read_db(repo / "instance" / "app.db") == "LEGACY-BACKUP"


# --- the admin page runs the same script ------------------------------------

def test_the_admin_update_runs_the_same_script(seeded, admin_client, app,
                                               monkeypatch):
    import re
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "ok", ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Done()

    sent = []
    monkeypatch.setattr("app.blueprints.admin.update.send_mail",
                        lambda **kw: sent.append(kw) or True)
    monkeypatch.setattr("app.blueprints.admin.update.subprocess.run", fake_run)

    admin_client.post("/admin/update-request")
    code = re.search(r"Confirmation code: (\d{6})", sent[-1]["body"]).group(1)
    admin_client.post("/admin/update-confirm", data={"code": code})

    assert len(calls) == 1
    assert calls[0][1].endswith("scripts/site_update.py")
    assert calls[0][2:] == ["update", "--no-wait"]
