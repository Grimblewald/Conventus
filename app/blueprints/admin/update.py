"""Admin → Update site (git pull + migrate)."""
from __future__ import annotations

import secrets
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user

from . import admin_bp
from ...extensions import db
from ...models import OTPCode
from ...security import requires_permission, audit
from ...services.mail import send_mail
from ...services.updater import latest_status


def _run_git_pull() -> tuple[bool, str]:
    root = Path(current_app.root_path).parent
    try:
        out = subprocess.check_output(
            ["git", "pull"], cwd=str(root),
            stderr=subprocess.STDOUT, timeout=60,
        )
        return (True, out.decode().strip())
    except subprocess.CalledProcessError as e:
        return (False, e.output.decode().strip() if e.output else str(e))
    except Exception as e:
        return (False, str(e))


def _run_migrations() -> None:
    root = Path(current_app.root_path).parent
    subprocess.run(
        ["uv", "run", "flask", "--app", "wsgi:app", "db", "upgrade"],
        cwd=str(root), check=True, capture_output=True, text=True,
    )


@admin_bp.route("/update")
@requires_permission("system.backup")
def update_page():
    status = latest_status()
    return render_template("admin/update.html", status=status)


@admin_bp.route("/update-request", methods=["POST"])
@requires_permission("system.backup")
def update_request():
    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    ok = send_mail(
        to=current_user.email,
        subject="Confirm site update",
        body=(f"You requested to update the site from git.\n\n"
              f"Confirmation code: {code}\n\n"
              f"This code expires in {ttl // 60} minutes. "
              f"If you didn't request this, ignore the email.\n\n"
              f"This will run git pull and apply database migrations."),
    )
    if not ok:
        flash("Failed to send confirmation email. Please try again.", "error")
        return redirect(url_for("admin.update_page"))
    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="site_update",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    flash("A confirmation code has been sent to your email.", "success")
    return redirect(url_for("admin.update_confirm"))


@admin_bp.route("/update-confirm", methods=["GET", "POST"])
@requires_permission("system.backup")
def update_confirm():
    if request.method == "POST":
        entered = (request.form.get("code") or "").strip().replace(" ", "")
        otp = (OTPCode.query
               .filter_by(email=current_user.email.lower(),
                          code=entered,
                          purpose="site_update",
                          consumed_at=None)
               .order_by(OTPCode.id.desc())
               .first())
        if not (otp and otp.is_valid()):
            flash("That code didn't match, or it has expired.", "error")
            return render_template("admin/update_confirm.html")
        otp.consumed_at = datetime.utcnow()

        ok, output = _run_git_pull()
        if not ok:
            flash(f"git pull failed: {output}", "error")
            return redirect(url_for("admin.update_page"))

        try:
            _run_migrations()
        except Exception as e:
            flash(f"Migrations failed: {e}", "error")
            return redirect(url_for("admin.update_page"))

        audit.record("site.updated",
                     target_kind="system", target_id=0,
                     summary="Site updated via admin panel")
        flash("Site updated. You may need to restart the app.", "success")
        return redirect(url_for("admin.index"))

    return render_template("admin/update_confirm.html")
