"""Admin → Backup & restore (full-site archives, chunked transfers).

Archive building/validation/restoring lives in ``app.services.backup_archive``
(shared with ``scripts/backup.py``); this module owns the HTTP surface:
chunked download/upload, OTP confirmation, and the password-protected
full backup that includes .env.
"""
from __future__ import annotations

import hashlib
import secrets
import shutil
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
from ...services.backup_archive import (
    MIN_ENV_PASSWORD_LEN, build_backup_zip, is_encrypted_zip,
    restore_backup_zip, validate_backup_zip,
)
from ...services.mail import send_mail


# ---------------------------------------------------------------------------
# Chunk size — keep under Flask's MAX_CONTENT_LENGTH (16 MB) and under
# Cloudflare's free-tier request limit (100 MB).
# ---------------------------------------------------------------------------
CHUNK_BYTES = 15 * 1024 * 1024  # 15 MB (leaves room for multipart overhead)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _backup_dir() -> Path:
    return _ensure_dir(Path(current_app.root_path).parent / "backups")


# ---------------------------------------------------------------------------
# Chunking helpers
# ---------------------------------------------------------------------------

def _chunk_dir(upload_id: str) -> Path:
    return _ensure_dir(_backup_dir() / ".chunks" / upload_id)


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


def _clear_stale_chunks() -> None:
    # Only the latest backup's chunks are kept.
    chunks_dir = _backup_dir() / ".chunks"
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)


def _chunked_download_response(zip_path: Path):
    """Split *zip_path* for chunked download and return the JSON handle.
    The source zip is removed once split."""
    _clear_stale_chunks()
    upload_id = secrets.token_hex(12)
    chunk_count, total_hash = _split_file(zip_path, upload_id)
    filename = zip_path.name
    zip_path.unlink(missing_ok=True)
    return jsonify({
        "upload_id": upload_id,
        "chunk_count": chunk_count,
        "total_hash": total_hash,
        "filename": filename,
    })


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
        zip_path = build_backup_zip(_backup_dir())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    audit.record("backup.created",
                 target_kind="system", target_id=0,
                 summary=f"Backup created: {zip_path.name}")
    return _chunked_download_response(zip_path)


@admin_bp.route("/backup/download-chunk/<upload_id>/<int:index>")
@requires_permission("system.backup")
def backup_download_chunk(upload_id: str, index: int):
    chunk_path = _chunk_dir(upload_id) / f"{index:04d}"
    if not chunk_path.exists():
        abort(404)
    return send_file(chunk_path, mimetype="application/octet-stream")


# -- Full backup including .env (password-protected, OTP-gated) ---------------

@admin_bp.route("/backup/env-request", methods=["POST"])
@requires_permission("system.backup")
def backup_env_request():
    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    ok = send_mail(
        to=current_user.email,
        subject="Confirm full backup (includes .env)",
        body=(f"You requested a FULL site backup that includes the .env file "
              f"with all server secrets.\n\n"
              f"Confirmation code: {code}\n\n"
              f"This code expires in {ttl // 60} minutes. "
              f"If you didn't request this, ignore the email."),
    )
    if not ok:
        flash("Failed to send confirmation email. Please try again.", "error")
        return redirect(url_for("admin.backup"))
    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="backup_env",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    flash("A confirmation code has been sent to your email.", "success")
    return redirect(url_for("admin.backup_env_confirm"))


@admin_bp.route("/backup/env-confirm")
@requires_permission("system.backup")
def backup_env_confirm():
    return render_template("admin/backup_env_confirm.html",
                           min_password_len=MIN_ENV_PASSWORD_LEN)


@admin_bp.route("/backup/env-create", methods=["POST"])
@requires_permission("system.backup")
def backup_env_create():
    entered = (request.form.get("code") or "").strip().replace(" ", "")
    password = request.form.get("password") or ""
    password2 = request.form.get("password2") or ""

    if len(password) < MIN_ENV_PASSWORD_LEN:
        return jsonify({"error": f"Password must be at least "
                                 f"{MIN_ENV_PASSWORD_LEN} characters."}), 400
    if password != password2:
        return jsonify({"error": "Passwords do not match."}), 400

    otp = (OTPCode.query
           .filter_by(email=current_user.email.lower(),
                      code=entered,
                      purpose="backup_env",
                      consumed_at=None)
           .order_by(OTPCode.id.desc())
           .first())
    if not (otp and otp.is_valid()):
        return jsonify({"error": "That code didn't match, or it has expired."}), 400
    otp.consumed_at = datetime.utcnow()
    db.session.commit()

    try:
        zip_path = build_backup_zip(_backup_dir(), include_env=True,
                                    password=password)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    audit.record("backup.env_included_created",
                 target_kind="system", target_id=0,
                 summary=f"Full backup incl. .env created by {current_user.email}: "
                         f"{zip_path.name}")
    return _chunked_download_response(zip_path)


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
        final_path = _backup_dir() / f".restore-{upload_id}.zip"
        recombined_hash = _recombine_chunks(upload_id, final_path)
        err = validate_backup_zip(final_path)
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

    restore_path = _backup_dir() / f".restore-{upload_id}.zip"

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
    restore_path = _backup_dir() / f".restore-{token}.zip"

    if not restore_path.exists():
        flash("That restore session has expired or is invalid.", "error")
        return redirect(url_for("admin.backup"))

    encrypted = is_encrypted_zip(restore_path)

    if request.method == "POST":
        entered = (request.form.get("code") or "").strip().replace(" ", "")
        password = request.form.get("backup_password") or None
        otp = (OTPCode.query
               .filter_by(email=current_user.email.lower(),
                          code=entered,
                          purpose="backup_restore",
                          consumed_at=None)
               .order_by(OTPCode.id.desc())
               .first())
        if not (otp and otp.is_valid()):
            flash("That code didn't match, or it has expired.", "error")
            return render_template("admin/backup_restore_confirm.html",
                                   token=token, encrypted=encrypted)
        if encrypted and not password:
            flash("This backup is password-protected — enter its password.",
                  "error")
            return render_template("admin/backup_restore_confirm.html",
                                   token=token, encrypted=encrypted)

        otp.consumed_at = datetime.utcnow()

        try:
            safety_zip = build_backup_zip(_backup_dir())
            audit.record("backup.safety_created",
                         target_kind="system", target_id=0,
                         summary=f"Pre-restore safety backup: {safety_zip.name}")
        except Exception:
            pass

        try:
            warnings = restore_backup_zip(restore_path, password=password)
            # Dispose engine — the DB file was replaced, so existing
            # connections point to the old (unlinked) file.
            db.engine.dispose()
            try:
                audit.record("backup.restored",
                             target_kind="system", target_id=0,
                             summary="Site restored from backup")
            except Exception:
                pass
            for w in warnings:
                flash(f"Restore note: {w}", "warning")
            flash("Site restored successfully. You may need to restart the app.",
                  "success")
            return redirect(url_for("admin.index"))
        except RuntimeError as e:
            if "password" in str(e).lower():
                flash("Restore failed: wrong backup password.", "error")
            else:
                flash(f"Restore failed: {e}", "error")
            return redirect(url_for("admin.backup"))
        except Exception as e:
            flash(f"Restore failed: {e}", "error")
            return redirect(url_for("admin.backup"))
        finally:
            restore_path.unlink(missing_ok=True)

    return render_template("admin/backup_restore_confirm.html",
                           token=token, encrypted=encrypted)
