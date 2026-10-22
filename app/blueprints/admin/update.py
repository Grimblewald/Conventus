"""Admin → Update site, through scripts/site_update.py — the same code
scripts/update.sh runs, restore point included."""
from __future__ import annotations

import secrets
import subprocess
import sys
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


def _run_update() -> tuple[bool, str]:
    """Restore point → pull → dependencies → migrate → queued restart.

    The restart is queued rather than awaited: it stops this very process.
    """
    root = Path(current_app.root_path).parent
    try:
        proc = subprocess.run(
            [sys.executable, str(root / "scripts" / "site_update.py"),
             "update", "--no-wait"],
            cwd=str(root), capture_output=True, text=True, timeout=900)
    except Exception as e:
        return (False, str(e))
    return (proc.returncode == 0, (proc.stdout + proc.stderr).strip())


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
              f"This writes a restore point, then pulls the new version, "
              f"applies database migrations and restarts the site."),
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
        # Committed before the update starts: its restart ends this process.
        db.session.commit()
        # Recorded first for the same reason; a failure is recorded below.
        audit.record("site.updated", target_kind="system", target_id=0,
                     summary=f"Site update started via admin panel by "
                             f"{current_user.email}")

        ok, output = _run_update()
        if not ok:
            audit.record("site.update_failed", target_kind="system",
                         target_id=0, summary=output[-400:])
            flash("The update stopped. Nothing after the failed step ran, and "
                  "the restore point is in var/backups. If the site misbehaves, "
                  "run scripts/update.sh --revert on the server.", "error")
            flash(output[-2000:], "error")
            return redirect(url_for("admin.update_page"))
        # Render the waiting page directly — a redirect would race the
        # restart and greet the admin with a bad gateway.
        return render_template("admin/update_restarting.html")

    return render_template("admin/update_confirm.html")
