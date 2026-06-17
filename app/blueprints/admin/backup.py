"""Admin → Backup & restore (full-site archives, chunked transfers)."""
from __future__ import annotations

import hashlib
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
    abort, current_app, flash, jsonify, redirect, render_template,
    request, send_file, url_for,
)
from flask_login import current_user

from . import admin_bp
from ...extensions import db
from ...models import OTPCode
from ...security import requires_permission, audit
from ...services.mail import send_mail


# ---------------------------------------------------------------------------
# Chunk size — keep under Flask's MAX_CONTENT_LENGTH (16 MB) and under
# Cloudflare's free-tier request limit (100 MB).
# ---------------------------------------------------------------------------
CHUNK_BYTES = 15 * 1024 * 1024  # 15 MB (leaves room for multipart overhead)

# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Database helpers
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


# ---------------------------------------------------------------------------
# Chunking helpers
# ---------------------------------------------------------------------------

def _chunk_dir(upload_id: str) -> Path:
    return _ensure_dir(
        Path(current_app.root_path).parent / "backups" / ".chunks" / upload_id)


def _split_file(src: Path, upload_id: str) -> tuple[int, str]:
    """Split *src* into CHUNK_BYTES chunks under the chunk directory.
    Returns (chunk_count, total_sha256)."""
    d = _chunk_dir(upload_id)
    h = hashlib.sha256()
    idx = 0
    with open(src, "rb") as f:
        while True:
            data = f.read(CHUNK_BYTES)
            if not data:
                break
            h.update(data)
            (d / f"{idx:04d}").write_bytes(data)
            idx += 1
    return (idx, h.hexdigest())


def _recombine_chunks(upload_id: str, dest: Path) -> str:
    """Concatenate all chunks for *upload_id* into *dest*.
    Returns the SHA-256 of the recombined file."""
    d = _chunk_dir(upload_id)
    h = hashlib.sha256()
    with open(dest, "wb") as out:
        for i in range(100_000):  # safety cap
            chunk_path = d / f"{i:04d}"
            if not chunk_path.exists():
                break
            data = chunk_path.read_bytes()
            h.update(data)
            out.write(data)
    return h.hexdigest()


def _cleanup_chunks(upload_id: str) -> None:
    d = _chunk_dir(upload_id)
    if d.exists():
        shutil.rmtree(d)


# ---------------------------------------------------------------------------
# Backup zip builder
# ---------------------------------------------------------------------------

def _build_backup_zip() -> Path:
    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])
    instance_dir = Path(current_app.instance_path)
    backup_dir = _ensure_dir(Path(current_app.root_path).parent / "backups")

    stamp = datetime.utcnow().strftime("%Y-%m-%d-%H%M%S")
    zip_path = backup_dir / f"backup-{stamp}.zip"
    url = _database_url()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        if _is_sqlite(url):
            db_src = _sqlite_path(url)
            if not db_src.exists():
                raise RuntimeError(f"Database not found at {db_src}")
            db_dest = tmp_path / "app.db"
            _sqlite_copy(db_src, db_dest)
        else:
            db_dest = tmp_path / "app.sql"
            _pg_dump(url, db_dest)

        (tmp_path / "migration_head.txt").write_text(_current_alembic_head())

        setup_flag = instance_dir / ".setup-complete"
        if setup_flag.exists():
            shutil.copy2(setup_flag, tmp_path / ".setup-complete")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_dest, db_dest.name)
            flag_path = tmp_path / ".setup-complete"
            if flag_path.exists():
                zf.write(flag_path, "instance/.setup-complete")
            zf.write(tmp_path / "migration_head.txt", "migration_head.txt")
            if uploads_root.exists():
                for f in sorted(uploads_root.rglob("*")):
                    if f.is_file():
                        zf.write(f, f"uploads/{f.relative_to(uploads_root)}")

    return zip_path


# ---------------------------------------------------------------------------
# Validation & restore
# ---------------------------------------------------------------------------

def _validate_backup_zip(zip_path: Path) -> str | None:
    if not zipfile.is_zipfile(zip_path):
        return "Not a valid zip archive."
    with zipfile.ZipFile(zip_path, "r") as zf:
        has_db = any(n in ("app.db", "app.sql") for n in zf.namelist())
        if not has_db:
            return "Backup does not contain a database file (app.db or app.sql)."
    return None


def _perform_restore(zip_path: Path) -> None:
    url = _database_url()
    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])
    instance_dir = Path(current_app.instance_path)
    root = Path(current_app.root_path).parent

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())

        if "app.db" in names and _is_sqlite(url):
            db_dest = _sqlite_path(url)
            db_dest.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(zf.read("app.db"))
            shutil.move(tmp.name, str(db_dest))
        elif "app.sql" in names and not _is_sqlite(url):
            with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as tmp:
                tmp.write(zf.read("app.sql"))
            _pg_restore(Path(tmp.name), url)
            Path(tmp.name).unlink(missing_ok=True)

        upload_entries = [n for n in names if n.startswith("uploads/") and not n.endswith("/")]
        if upload_entries:
            if uploads_root.exists():
                shutil.rmtree(uploads_root)
            uploads_root.mkdir(parents=True, exist_ok=True)
            for entry in upload_entries:
                dest = root / entry
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(entry))

        if "instance/.setup-complete" in names:
            flag_dest = instance_dir / ".setup-complete"
            flag_dest.parent.mkdir(parents=True, exist_ok=True)
            flag_dest.write_bytes(zf.read("instance/.setup-complete"))

    _run_migrations()
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


# -- Download -----------------------------------------------------------------

@admin_bp.route("/backup/create", methods=["POST"])
@requires_permission("system.backup")
def backup_create():
    try:
        zip_path = _build_backup_zip()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    audit.record("backup.created",
                 target_kind="system", target_id=0,
                 summary=f"Backup created: {zip_path.name}")

    # Always split into chunks — avoids client-side JSON-vs-binary sniffing.
    upload_id = secrets.token_hex(12)
    chunk_count, total_hash = _split_file(zip_path, upload_id)
    zip_path.unlink(missing_ok=True)

    return jsonify({
        "upload_id": upload_id,
        "chunk_count": chunk_count,
        "total_hash": total_hash,
        "filename": zip_path.name,
    })


@admin_bp.route("/backup/download-chunk/<upload_id>/<int:index>")
@requires_permission("system.backup")
def backup_download_chunk(upload_id: str, index: int):
    chunk_path = _chunk_dir(upload_id) / f"{index:04d}"
    if not chunk_path.exists():
        abort(404)
    return send_file(chunk_path, mimetype="application/octet-stream")


# -- Restore ------------------------------------------------------------------

@admin_bp.route("/backup/restore-chunk", methods=["POST"])
@requires_permission("system.backup")
def backup_restore_chunk():
    upload_id = (request.form.get("upload_id") or "").strip()
    chunk_index = request.form.get("chunk_index", type=int)
    total_chunks = request.form.get("total_chunks", type=int)
    client_hash = (request.form.get("checksum") or "").strip().lower()

    f = request.files.get("chunk")
    if not upload_id or chunk_index is None or not f:
        return jsonify({"error": "Missing upload_id, chunk_index, or chunk file."}), 400

    data = f.stream.read()
    if _sha256(data) != client_hash:
        return jsonify({"error": f"Checksum mismatch on chunk {chunk_index}."}), 400

    d = _chunk_dir(upload_id)
    (d / f"{chunk_index:04d}").write_bytes(data)

    if chunk_index == total_chunks - 1:
        # Recombine into full zip
        backup_dir = _ensure_dir(Path(current_app.root_path).parent / "backups")
        final_path = backup_dir / f".restore-{upload_id}.zip"
        recombined_hash = _recombine_chunks(upload_id, final_path)
        err = _validate_backup_zip(final_path)
        _cleanup_chunks(upload_id)
        if err:
            final_path.unlink(missing_ok=True)
            return jsonify({"error": err}), 400
        return jsonify({
            "complete": True,
            "upload_id": upload_id,
            "total_hash": recombined_hash,
        })

    return jsonify({"complete": False, "chunk_index": chunk_index})


@admin_bp.route("/backup/restore-finalize", methods=["POST"])
@requires_permission("system.backup")
def backup_restore_finalize():
    """Called after all chunks are uploaded.  Validates the recombined zip,
    generates an OTP, and redirects to the confirm page."""
    upload_id = (request.form.get("upload_id") or "").strip()

    if not upload_id:
        flash("Missing upload session.", "error")
        return redirect(url_for("admin.backup"))

    backup_dir = _ensure_dir(Path(current_app.root_path).parent / "backups")
    restore_path = backup_dir / f".restore-{upload_id}.zip"

    if not restore_path.exists():
        flash("Upload session not found. The chunks may not all have arrived.",
              "error")
        return redirect(url_for("admin.backup"))

    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    ok = send_mail(
        to=current_user.email,
        subject="Confirm site restore",
        body=(f"You requested to restore the site from a backup.\n\n"
              f"Confirmation code: {code}\n\n"
              f"This code expires in {ttl // 60} minutes. "
              f"If you didn't request this, ignore the email.\n\n"
              f"WARNING: This will replace the current database and all "
              f"uploaded files."),
    )
    if not ok:
        flash("Failed to send confirmation email. Please try again.", "error")
        return redirect(url_for("admin.backup"))
    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="backup_restore",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    flash("Backup validated. A confirmation code has been sent to your email.",
          "success")
    return redirect(url_for("admin.backup_restore_confirm", token=upload_id))


@admin_bp.route("/backup/restore-confirm/<token>", methods=["GET", "POST"])
@requires_permission("system.backup")
def backup_restore_confirm(token: str):
    backup_dir = _ensure_dir(Path(current_app.root_path).parent / "backups")
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

        try:
            safety_zip = _build_backup_zip()
            audit.record("backup.safety_created",
                         target_kind="system", target_id=0,
                         summary=f"Pre-restore safety backup: {safety_zip.name}")
        except Exception:
            pass

        try:
            _perform_restore(restore_path)
            # Dispose engine — the DB file was replaced, so existing
            # connections point to the old (unlinked) file.
            db.engine.dispose()
            try:
                audit.record("backup.restored",
                             target_kind="system", target_id=0,
                             summary="Site restored from backup")
            except Exception:
                pass
            flash("Site restored successfully. You may need to restart the app.",
                  "success")
            return redirect(url_for("admin.index"))
        except Exception as e:
            flash(f"Restore failed: {e}", "error")
            return redirect(url_for("admin.backup"))
        finally:
            restore_path.unlink(missing_ok=True)

    return render_template("admin/backup_restore_confirm.html", token=token)
