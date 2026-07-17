"""Admin blueprint package.

The admin surface is large enough that we keep each concern in its own
module under `app/blueprints/admin/` and stitch them together here.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models import (
    Abstract, Announcement, Conference, OTPCode, Registration, User,
)
from ...security import requires_permission, staff_required, can
from ...services.mail import send_mail
from ...services.updater import latest_status


admin_bp = Blueprint("admin", __name__, template_folder="../../templates/admin")


# Expose `can()` as a Jinja global so admin templates can hide buttons that
# the current user is not permitted to use.
@admin_bp.app_context_processor
def _inject_can():
    return {"can": can}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@admin_bp.route("/")
@staff_required
def index():
    stats = {
        "members": User.query.filter(User.deleted_at.is_(None)).count(),
        "conferences": Conference.query.filter(Conference.deleted_at.is_(None)).count(),
        "abstracts": Abstract.query.filter(Abstract.deleted_at.is_(None)).count(),
        "registrations": Registration.query.filter(Registration.deleted_at.is_(None)).count(),
        "pending_abstracts": (
            Abstract.query
            .filter(Abstract.deleted_at.is_(None), Abstract.status == "submitted")
            .count()
        ),
    }
    recent_abs = (
        Abstract.query
        .filter(Abstract.deleted_at.is_(None))
        .order_by(Abstract.created_at.desc())
        .limit(8).all()
    )
    recent_regs = (
        Registration.query
        .filter(Registration.deleted_at.is_(None))
        .order_by(Registration.created_at.desc())
        .limit(8).all()
    )
    recent_anns = (
        Announcement.query
        .filter(Announcement.deleted_at.is_(None))
        .order_by(Announcement.published_at.desc())
        .limit(5).all()
    )
    return render_template(
        "admin/index.html",
        stats=stats,
        recent_abs=recent_abs,
        recent_regs=recent_regs,
        recent_anns=recent_anns,
        update=latest_status(),
    )


# ---------------------------------------------------------------------------
# Registrations list — view and manually mark as paid.
# ---------------------------------------------------------------------------

@admin_bp.route("/registrations")
@requires_permission("registrations.view")
def registrations():
    conference_id = request.args.get("conference_id", type=int)
    status_filter = request.args.get("status", "all")
    query = Registration.query.filter(Registration.deleted_at.is_(None))
    if conference_id:
        query = query.filter_by(conference_id=conference_id)
    if status_filter != "all":
        query = query.filter_by(status=status_filter)
    regs = query.order_by(Registration.created_at.desc()).all()
    conf_list = (
        Conference.query
        .filter(Conference.deleted_at.is_(None))
        .order_by(Conference.start_date.desc())
        .all()
    )
    return render_template(
        "admin/registrations.html",
        regs=regs,
        conferences=conf_list,
        conference_id=conference_id,
        status_filter=status_filter,
    )


@admin_bp.route("/registrations/<int:reg_id>/status", methods=["POST"])
@requires_permission("registrations.edit")
def registration_status(reg_id):
    reg = Registration.query.get_or_404(reg_id)
    new_status = (request.form.get("status") or "").strip()
    if new_status in ("pending", "paid", "refunded", "cancelled", "failed", "processing"):
        reg.status = new_status
        db.session.commit()
        from ...security import audit as audit_log
        audit_log.record(
            "registration.status_changed",
            target_kind="registration", target_id=reg.id,
            summary=f"{current_user.email} → {reg.status}",
        )
        flash(f"Registration marked as {new_status}.", "success")
    return redirect(url_for("admin.registrations"))


@admin_bp.route("/registrations/<int:reg_id>")
@requires_permission("registrations.view")
def registration_detail(reg_id):
    reg = Registration.query.get_or_404(reg_id)
    conference = reg.conference
    schema = conference.registration_form_schema if conference else None
    sub_events_list = conference.sub_events if conference else []
    return render_template(
        "admin/registration_detail.html",
        reg=reg, conference=conference,
        schema=schema, sub_events_list=sub_events_list,
    )


@admin_bp.route("/registrations/<int:reg_id>/delete-request", methods=["POST"])
@requires_permission("registrations.edit")
def registration_delete_request(reg_id):
    reg = Registration.query.get_or_404(reg_id)
    if reg.deleted_at is not None:
        flash("This registration has already been deleted.", "error")
        return redirect(url_for("admin.registrations"))
    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    ok = send_mail(
        to=current_user.email,
        subject="Confirm registration deletion",
        body=(f"You requested to delete a registration by "
              f"{reg.user.email if reg.user else 'unknown'} "
              f"for \"{reg.conference.title if reg.conference else '?'}\".\n\n"
              f"Confirmation code: {code}\n\n"
              f"This code expires in {ttl // 60} minutes. "
              f"If you didn't request this, ignore the email."),
    )
    if not ok:
        flash("Failed to send confirmation email. Please try again.", "error")
        return redirect(url_for("admin.registration_detail", reg_id=reg.id))
    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="registration_delete",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    flash("A confirmation code has been sent to your email.", "success")
    return redirect(url_for("admin.registration_delete_confirm", reg_id=reg.id))


@admin_bp.route("/registrations/<int:reg_id>/delete-confirm", methods=["GET", "POST"])
@requires_permission("registrations.edit")
def registration_delete_confirm(reg_id):
    reg = Registration.query.get_or_404(reg_id)
    if reg.deleted_at is not None:
        flash("This registration has already been deleted.", "error")
        return redirect(url_for("admin.registrations"))
    if request.method == "POST":
        entered = (request.form.get("code") or "").strip().replace(" ", "")
        otp = (OTPCode.query
               .filter_by(email=current_user.email.lower(),
                          code=entered,
                          purpose="registration_delete",
                          consumed_at=None)
               .order_by(OTPCode.id.desc())
               .first())
        if not (otp and otp.is_valid()):
            flash("That code didn't match, or it has expired.", "error")
            return render_template("admin/registration_delete_confirm.html", reg=reg)
        otp.consumed_at = datetime.utcnow()
        summary = f"{reg.user.email if reg.user else '?'} → {reg.conference.title if reg.conference else '?'}"
        reg.deleted_at = datetime.utcnow()
        db.session.commit()
        from ...security import audit as audit_log
        audit_log.record("registration.deleted",
                         target_kind="registration", target_id=reg.id,
                         summary=f"Deleted {summary}")
        flash(f"Deleted registration for {summary}.", "success")
        return redirect(url_for("admin.registrations"))
    return render_template("admin/registration_delete_confirm.html", reg=reg)


# ---------------------------------------------------------------------------
# Sub-modules: each registers more routes on the same blueprint.
# Imported for their side effects.
# ---------------------------------------------------------------------------
from . import site          # noqa: F401, E402
from . import pages         # noqa: F401, E402
from . import nav           # noqa: F401, E402
from . import footer        # noqa: F401, E402
from . import committee     # noqa: F401, E402
from . import conferences   # noqa: F401, E402
from . import announcements # noqa: F401, E402
from . import users         # noqa: F401, E402
from . import permissions   # noqa: F401, E402
from . import audit         # noqa: F401, E402
from . import api           # noqa: F401, E402
from . import sponsors      # noqa: F401, E402
from . import past_boards   # noqa: F401, E402
from . import backup        # noqa: F401, E402
from . import update        # noqa: F401, E402
from . import form_builder   # noqa: F401, E402
