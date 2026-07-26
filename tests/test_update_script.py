"""Data-safety guarantees for scripts/update.sh.

Regression cover for the 2026-07-22 incident: reverting a broken deploy lost
data. The script drove a single backup slot (`db.bak`) overwritten on every
run, so a second update during an incident destroyed the good snapshot, and
`--revert` overwrote the live database with no safety copy — an irreversible
roll-back of everything written since the last update.

These tests drive the REAL script against a throwaway git repo and database
(plain text files stand in for app.db — the script only ever copies bytes), so
the guarantees are verified end to end and cannot silently regress. The service
restart at the end of a real run is never reached here: every path exercised
stops before it, or the harness only runs the backup/revert logic.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "update.sh"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A minimal git repo laid out like the project: instance/app.db plus the
    script under scripts/, with git identity configured."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "update.sh")
    os.chmod(tmp_path / "scripts" / "update.sh", 0o755)
    (tmp_path / "instance").mkdir()
    (tmp_path / "instance" / "app.db").write_text("data-v1")

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.org")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README").write_text("v1")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "v1")
    return tmp_path


def _backup_dirs(repo):
    d = repo / "var" / "backups"
    if not d.exists():
        return []
    return sorted(p for p in d.iterdir() if p.is_dir())


@pytest.fixture
def shimmed_path(tmp_path):
    """A PATH whose git pull / systemctl / fuser are no-ops, so the real script
    runs to completion without touching a remote or a service."""
    bindir = tmp_path / "shim-bin"
    bindir.mkdir()
    # git: pass through except `pull`, which must be a no-op success.
    (bindir / "git").write_text(
        '#!/usr/bin/env bash\n'
        'if [ "$1" = "pull" ]; then exit 0; fi\n'
        'exec /usr/bin/git "$@"\n')
    for name in ("systemctl", "fuser"):
        (bindir / name).write_text('#!/usr/bin/env bash\nexit 0\n')
    # uv: swallow the migration commands (`uv run flask db …`).
    (bindir / "uv").write_text(
        '#!/usr/bin/env bash\n'
        # `flask db current` must print a revision so the stamp branch is skipped.
        'for a in "$@"; do if [ "$a" = "current" ]; then echo "abc123 (head)"; exit 0; fi; done\n'
        'exit 0\n')
    for f in bindir.iterdir():
        os.chmod(f, 0o755)
    return f"{bindir}:{os.environ['PATH']}"


def _run(repo, shimmed_path, *args):
    return subprocess.run(
        ["bash", str(repo / "scripts" / "update.sh"), *args],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "PATH": shimmed_path, "HOME": str(repo)})


def test_update_takes_a_timestamped_snapshot(repo, shimmed_path):
    r = _run(repo, shimmed_path)
    assert r.returncode == 0, r.stderr
    snaps = _backup_dirs(repo)
    assert len(snaps) == 1
    assert (snaps[0] / "app.db").read_text() == "data-v1"
    assert (snaps[0] / "git-head").read_text().strip()


def test_second_update_does_not_clobber_the_first_snapshot(repo, shimmed_path):
    """The core of the incident: a second update must NOT overwrite the good
    backup taken by the first."""
    _run(repo, shimmed_path)
    (repo / "instance" / "app.db").write_text("data-v2-with-live-writes")
    _run(repo, shimmed_path)

    snaps = _backup_dirs(repo)
    contents = sorted((s / "app.db").read_text() for s in snaps)
    # BOTH snapshots survive — the original good copy is still recoverable.
    assert "data-v1" in contents
    assert "data-v2-with-live-writes" in contents


def test_revert_preserves_the_current_db_before_rolling_back(repo, shimmed_path):
    """A revert must snapshot the live DB first, so the rollback is reversible
    and no state is ever destroyed irrecoverably."""
    _run(repo, shimmed_path)                       # snapshot of data-v1
    (repo / "instance" / "app.db").write_text("data-v2-live")

    r = _run(repo, shimmed_path, "--revert", "--yes")
    assert r.returncode == 0, r.stderr

    # Live DB rolled back to the snapshot…
    assert (repo / "instance" / "app.db").read_text() == "data-v1"
    # …but the pre-revert state was preserved and is still readable.
    pre = [s for s in _backup_dirs(repo) if s.name.endswith("pre-revert")]
    assert pre, "revert did not preserve the pre-revert database"
    assert (pre[0] / "app.db").read_text() == "data-v2-live"


def test_revert_without_yes_makes_no_change(repo, shimmed_path):
    _run(repo, shimmed_path)
    (repo / "instance" / "app.db").write_text("data-v2-live")
    # No --yes and stdin closed → prompt reads EOF → cancel.
    r = subprocess.run(
        ["bash", str(repo / "scripts" / "update.sh"), "--revert"],
        cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env={**os.environ, "PATH": shimmed_path, "HOME": str(repo)})
    assert r.returncode != 0
    assert (repo / "instance" / "app.db").read_text() == "data-v2-live"


def test_revert_with_no_snapshot_errors_cleanly(repo, shimmed_path):
    r = _run(repo, shimmed_path, "--revert", "--yes")
    assert r.returncode != 0
    assert "nothing to revert" in (r.stdout + r.stderr).lower()
    # The live DB is untouched.
    assert (repo / "instance" / "app.db").read_text() == "data-v1"


def test_old_snapshots_are_pruned_but_pre_revert_kept(repo, shimmed_path):
    env_path = shimmed_path
    for i in range(3):
        (repo / "instance" / "app.db").write_text(f"data-{i}")
        subprocess.run(
            ["bash", str(repo / "scripts" / "update.sh")],
            cwd=repo, capture_output=True, text=True,
            env={**os.environ, "PATH": env_path, "HOME": str(repo),
                 "UPDATE_KEEP_SNAPSHOTS": "2"})
    update_snaps = [s for s in _backup_dirs(repo)
                    if not s.name.endswith("pre-revert")]
    assert len(update_snaps) == 2, [s.name for s in update_snaps]


def test_pre_restore_snapshots_are_never_pruned(repo, shimmed_path):
    """The admin panel's archive restore writes its safety copy into the SAME
    var/backups directory (backup_archive._safety_snapshot). It is the only
    record of the database that restore overwrote, so update's pruning must
    never count it toward the keep limit and delete it."""
    backups = repo / "var" / "backups"
    backups.mkdir(parents=True)
    keeper = backups / "20260101-090000-pre-restore"
    keeper.mkdir()
    (keeper / "app.db").write_text("PRE-RESTORE-LIVE-DB")

    for i in range(3):
        (repo / "instance" / "app.db").write_text(f"data-{i}")
        subprocess.run(
            ["bash", str(repo / "scripts" / "update.sh")],
            cwd=repo, capture_output=True, text=True,
            env={**os.environ, "PATH": shimmed_path, "HOME": str(repo),
                 "UPDATE_KEEP_SNAPSHOTS": "1"})

    assert keeper.exists(), "pruning deleted the pre-restore safety copy"
    assert (keeper / "app.db").read_text() == "PRE-RESTORE-LIVE-DB"


def test_revert_never_restores_from_a_pre_restore_snapshot(repo, shimmed_path):
    """Same rule as the pre-revert copies, for the ones the admin panel writes.
    A pre-restore dir carries no git-head, so selecting it would also leave the
    code un-rolled-back while reporting a successful revert."""
    backups = repo / "var" / "backups"
    backups.mkdir(parents=True)

    upd = backups / "20260101-100000"
    upd.mkdir()
    (upd / "app.db").write_text("UPDATE-SNAPSHOT")
    (upd / "git-head").write_text("")

    for name in ("20260301-120000-pre-restore", "20260301-120000-pre-restore-1"):
        d = backups / name
        d.mkdir()
        (d / "app.db").write_text("OVERWRITTEN-BY-ARCHIVE-RESTORE")

    (repo / "instance" / "app.db").write_text("CURRENT-LIVE")
    r = _run(repo, shimmed_path, "--revert", "--yes")
    assert r.returncode == 0, r.stderr
    assert (repo / "instance" / "app.db").read_text() == "UPDATE-SNAPSHOT"


def test_revert_reports_when_snapshots_exist_but_hold_no_database(repo, shimmed_path):
    """Snapshot dirs with no app.db (every Postgres install: update.sh records
    code only) must produce the friendly error, not a silent death.

    `latest_snapshot` used to end on a bare `[ -f … ] && …`, so it returned 1
    when nothing matched — and under `set -euo pipefail` the assignment
    `SNAP="$(latest_snapshot)"` killed the script right there: exit 1, no
    output, no rollback, and the legacy db.bak fallback below unreachable.
    """
    backups = repo / "var" / "backups"
    backups.mkdir(parents=True)
    for name in ("20260101-100000", "20260102-100000"):
        d = backups / name
        d.mkdir()
        (d / "git-head").write_text("deadbeef")   # code-only snapshot

    r = _run(repo, shimmed_path, "--revert", "--yes")
    assert r.returncode != 0
    assert "nothing to revert" in (r.stdout + r.stderr).lower(), \
        f"script died silently: stdout={r.stdout!r} stderr={r.stderr!r}"
    assert (repo / "instance" / "app.db").read_text() == "data-v1"


def test_revert_falls_back_to_a_legacy_single_slot_backup(repo, shimmed_path):
    """Reachability of the pre-2026-07 db.bak path, which sat immediately after
    the `SNAP="$(latest_snapshot)"` assignment that used to abort the script."""
    backups = repo / "var" / "backups"
    backups.mkdir(parents=True)
    (backups / "db.bak").write_text("LEGACY-BACKUP")

    (repo / "instance" / "app.db").write_text("CURRENT-LIVE")
    r = _run(repo, shimmed_path, "--revert", "--yes")
    assert r.returncode == 0, r.stderr
    assert (repo / "instance" / "app.db").read_text() == "LEGACY-BACKUP"


def test_revert_never_restores_from_a_pre_revert_snapshot(repo, shimmed_path):
    """Pre-revert copies (including the same-second collision form
    <stamp>-pre-revert-N) are rolled-back live state, never a rollback target —
    a --revert must restore the newest UPDATE snapshot, skipping both."""
    backups = repo / "var" / "backups"
    backups.mkdir(parents=True)

    # An older genuine update snapshot — the correct restore target.
    upd = backups / "20260101-100000"
    upd.mkdir()
    (upd / "app.db").write_text("UPDATE-SNAPSHOT")
    (upd / "git-head").write_text("")

    # Two pre-revert copies in the same second: the base and the -1 collision
    # form. Newer by name than the update snapshot, so a naive "newest dir"
    # pick would wrongly choose one of these.
    for name, body in (("20260201-120000-pre-revert", "ROLLED-BACK-A"),
                       ("20260201-120000-pre-revert-1", "ROLLED-BACK-B")):
        d = backups / name
        d.mkdir()
        (d / "app.db").write_text(body)

    (repo / "instance" / "app.db").write_text("CURRENT-LIVE")
    r = _run(repo, shimmed_path, "--revert", "--yes")
    assert r.returncode == 0, r.stderr
    # Restored the update snapshot, not either pre-revert copy.
    assert (repo / "instance" / "app.db").read_text() == "UPDATE-SNAPSHOT"
