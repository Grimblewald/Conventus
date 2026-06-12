"""Admin → Backup & restore (full-site archives)."""
from __future__ import annotations

import os
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    current_app, flash, redirect, render_template, request, send_file, url_for,
)
from flask_login import current_user

from . import admin_bp
from ...extensions import db
from ...models import OTPCode
from ...security import requires_permission, audit
from ...services.mail import send_mail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _database_url() -> str:
    raw = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    return (os.environ.get("DATABASE_URL") or raw or
            f"sqlite:///{current_app.instance_path}/app.db")


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _sqlite_path(url: str) -> Path:
    prefix = "sqlite:///"
    path = url[len(prefix):]
    if path.startswith("/"):
        return Path(path)
    return Path(current_app.root_path).parent / path


def _sqlite_copy(src: Path, dst: Path) -> None:
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    s.backup(d)
    d.close()
    s.close()


def _pg_dump(url: str, dest: Path) -> None:
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


def _pg_restore(dump_path: Path, url: str) -> None:
    from urllib.parse import unquote, urlparse

    url = url.replace("postgresql+psycopg://", "postgresql://")
    env = os.environ.copy()
    parsed = urlparse(url)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    subprocess.run(
        ["psql", "-d", url, "-f", str(dump_path)],
        check=True, env=env, capture_output=True, text=True,
    )


def _current_alembic_head() -> str:
    from sqlalchemy import text
    row = db.session.execute(text("SELECT version_num FROM alembic_version")).first()
    return row[0] if row else "unknown"


def _build_backup_zip() -> Path:
    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])
    instance_dir = Path(current_app.instance_path)
    backup_dir = Path(current_app.root_path).parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.utcnow().strftime("%Y-%m-%d-%H%M%S")
    zip_path = backup_dir / f"backup-{stamp}.zip"

    url = _database_url()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Database
        if _is_sqlite(url):
            db_src = _sqlite_path(url)
            if not db_src.exists():
                raise RuntimeError(f"Database not found at {db_src}")
            db_dest = tmp_path / "app.db"
            _sqlite_copy(db_src, db_dest)
        else:
            db_dest = tmp_path / "app.sql"
            _pg_dump(url, db_dest)

        # Migration head stamp
        (tmp_path / "migration_head.txt").write_text(_current_alembic_head())

        # Instance flags
        setup_flag = instance_dir / ".setup-complete"
        if setup_flag.exists():
            shutil.copy2(setup_flag, tmp_path / ".setup-complete")

        # Build zip
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_dest, db_dest.name)

            flag_path = tmp_path / ".setup-complete"
            if flag_path.exists():
                zf.write(flag_path, "instance/.setup-complete")

            mh_path = tmp_path / "migration_head.txt"
            zf.write(mh_path, "migration_head.txt")

            if uploads_root.exists():
                for f in sorted(uploads_root.rglob("*")):
                    if f.is_file():
                        arcname = f"uploads/{f.relative_to(uploads_root)}"
                        zf.write(f, arcname)

    return zip_path


def _validate_backup_zip(zip_path: Path) -> str | None:
    """Return an error message if the zip is invalid, or None if ok."""
    if not zipfile.is_zipfile(zip_path):
        return "Not a valid zip archive."
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        has_db = any(n == "app.db" or n == "app.sql" for n in names)
        if not has_db:
            return "Backup does not contain a database file (app.db or app.sql)."
    return None


def _perform_restore(zip_path: Path) -> None:
    """Replace DB, uploads, and instance flags from a validated backup zip."""
    url = _database_url()
    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])
    instance_dir = Path(current_app.instance_path)
    root = Path(current_app.root_path).parent

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())

        # --- Database ---
        if "app.db" in names and _is_sqlite(url):
            db_dest = _sqlite_path(url)
            db_dest.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(zf.read("app.db"))
            Path(tmp.name).replace(db_dest)
        elif "app.sql" in names and not _is_sqlite(url):
            with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as tmp:
                tmp.write(zf.read("app.sql"))
            _pg_restore(Path(tmp.name), url)
            Path(tmp.name).unlink(missing_ok=True)

        # --- Uploads ---
        upload_entries = [n for n in names if n.startswith("uploads/") and not n.endswith("/")]
        if upload_entries:
            if uploads_root.exists():
                shutil.rmtree(uploads_root)
            uploads_root.mkdir(parents=True, exist_ok=True)
            for entry in upload_entries:
                dest = root / entry
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(entry))

        # --- Instance flags ---
        if "instance/.setup-complete" in names:
            flag_dest = instance_dir / ".setup-complete"
            flag_dest.parent.mkdir(parents=True, exist_ok=True)
            flag_dest.write_bytes(zf.read("instance/.setup-complete"))

    # --- Run pending migrations ---
    _run_migrations()

    # --- Clear booklets cache (zips reference old abstracts) ---
    _clear_booklet_cache()


def _run_migrations() -> None:
    root = Path(current_app.root_path).parent
    subprocess.run(
        ["uv", "run", "flask", "--app", "wsgi:app", "db", "upgrade"],
        cwd=str(root), check=True, capture_output=True, text=True,
    )


def _clear_booklet_cache() -> None:
    cache_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "abstracts" / ".booklet-cache"
    if cache_dir.exists():
        for f in cache_dir.glob("*.zip"):
            f.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@admin_bp.route("/backup")
@requires_permission("system.backup")
def backup():
    return render_template("admin/backup.html")


@admin_bp.route("/backup/create", methods=["POST"])
@requires_permission("system.backup")
def backup_create():
    try:
        zip_path = _build_backup_zip()
        audit.record("backup.created",
                     target_kind="system", target_id=0,
                     summary=f"Backup created: {zip_path.name}")
        flash("Backup created.", "success")
        return send_file(
            zip_path, as_attachment=True,
            download_name=zip_path.name,
            mimetype="application/zip",
        )
    except Exception as e:
        flash(f"Backup failed: {e}", "error")
        return redirect(url_for("admin.backup"))


@admin_bp.route("/backup/restore-upload", methods=["POST"])
@requires_permission("system.backup")
def backup_restore_upload():
    f = request.files.get("backup_file")
    if not f or not f.filename:
        flash("No file uploaded.", "error")
        return redirect(url_for("admin.backup"))

    # Save upload to a temp location for OTP confirmation
    backup_dir = Path(current_app.root_path).parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(12)
    restore_path = backup_dir / f".restore-{token}.zip"

    raw = f.stream.read()
    restore_path.write_bytes(raw)

    err = _validate_backup_zip(restore_path)
    if err:
        restore_path.unlink(missing_ok=True)
        flash(err, "error")
        return redirect(url_for("admin.backup"))

    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="backup_restore",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    send_mail(
        to=current_user.email,
        subject="Confirm site restore",
        body=(f"You requested to restore the site from a backup.\n\n"
              f"Confirmation code: {code}\n\n"
              f"This code expires in {ttl // 60} minutes. "
              f"If you didn't request this, ignore the email.\n\n"
              f"WARNING: This will replace the current database and all "
              f"uploaded files."),
    )
    flash("Backup validated. A confirmation code has been sent to your email.",
          "success")
    return redirect(url_for("admin.backup_restore_confirm", token=token))


@admin_bp.route("/backup/restore-confirm/<token>", methods=["GET", "POST"])
@requires_permission("system.backup")
def backup_restore_confirm(token: str):
    backup_dir = Path(current_app.root_path).parent / "backups"
    restore_path = backup_dir / f".restore-{token}.zip"

    if not restore_path.exists():
        flash("That restore session has expired or is invalid.", "error")
        return redirect(url_for("admin.backup"))

    if request.method == "POST":
        entered = (request.form.get("code") or "").strip().replace(" ", "")
        otp = (OTPCode.query
               .filter_by(email=current_user.email.lower(),
                          code=entered,
                          purpose="backup_restore",
                          consumed_at=None)
               .order_by(OTPCode.id.desc())
               .first())
        if not (otp and otp.is_valid()):
            flash("That code didn't match, or it has expired.", "error")
            return render_template("admin/backup_restore_confirm.html", token=token)

        otp.consumed_at = datetime.utcnow()

        # Safety backup before destructive restore
        try:
            safety_zip = _build_backup_zip()
            audit.record("backup.safety_created",
                         target_kind="system", target_id=0,
                         summary=f"Pre-restore safety backup: {safety_zip.name}")
        except Exception:
            pass  # best-effort; don't block restore

        try:
            _perform_restore(restore_path)
            audit.record("backup.restored",
                         target_kind="system", target_id=0,
                         summary="Site restored from backup")
            flash("Site restored successfully. You may need to restart the app.",
                  "success")
            return redirect(url_for("admin.index"))
        except Exception as e:
            flash(f"Restore failed: {e}", "error")
            return redirect(url_for("admin.backup"))
        finally:
            restore_path.unlink(missing_ok=True)

    return render_template("admin/backup_restore_confirm.html", token=token)
