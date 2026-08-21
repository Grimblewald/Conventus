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

def _search_registrations(query, q: str):
    """Filter registrations by whatever the treasurer has in hand.

    A bank transfer arrives as a line on a statement: a reference the payer
    typed or pasted, and little else. So the search has to accept the payer's
    reference (REG-000123, or REG000123 with the punctuation eaten by the
    bank), a bare registration number, the gateway's transaction id, and the
    checkout's merchant reference — as well as the payer's name or email,
    since references get quoted wrong and a name is often all that survives.
    """
    import re as _re

    from ...models import PaymentEvent
    from ...models.user import User

    like = f"%{q}%"
    bare = _re.sub(r"[^A-Za-z0-9]", "", q)
    clauses = [
        User.full_name.ilike(like),
        User.email.ilike(like),
        Registration.transaction_id.ilike(like),
    ]

    # REG-000123 / REG000123 / 123 all resolve to the registration itself.
    digits = _re.sub(r"^REG", "", bare, flags=_re.IGNORECASE)
    if digits.isdigit():
        clauses.append(Registration.id == int(digits))

    # The checkout's merchant reference lives on the ledger, not here, and
    # carries separators a bank may have stripped — so match it stripped too.
    if bare:
        stripped = db.func.replace(
            db.func.replace(PaymentEvent.merchant_reference, "-", ""), "_", "")
        clauses.append(PaymentEvent.query
                       .filter(PaymentEvent.registration_id == Registration.id,
                               stripped.ilike(f"%{bare}%"))
                       .exists())

    return query.outerjoin(User, Registration.user_id == User.id).filter(
        db.or_(*clauses))


@admin_bp.route("/registrations")
@requires_permission("registrations.view")
def registrations():
    conference_id = request.args.get("conference_id", type=int)
    status_filter = request.args.get("status", "all")
    q = (request.args.get("q") or "").strip()
    query = Registration.query.filter(Registration.deleted_at.is_(None))
    if conference_id:
        query = query.filter_by(conference_id=conference_id)
    if status_filter != "all":
        query = query.filter_by(status=status_filter)
    if q:
        query = _search_registrations(query, q)
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
        q=q,
    )


@admin_bp.route("/registrations/<int:reg_id>/status", methods=["POST"])
@requires_permission("registrations.edit")
def registration_status(reg_id):
    reg = Registration.query.get_or_404(reg_id)
    new_status = (request.form.get("status") or "").strip()
    if new_status in ("pending", "paid", "refunded", "cancelled", "failed", "processing"):
        old_status = reg.status
        reg.status = new_status
        db.session.commit()
        from ...security import audit as audit_log
        audit_log.record(
            "registration.status_changed",
            target_kind="registration", target_id=reg.id,
            summary=f"{current_user.email} → {reg.status}",
        )

        # A payment settled outside the gateway — a bank transfer, cash at the
        # desk, a waiver — leaves no trace in the ledger, because no webhook
        # ever fires for it. Recording it under `manual.*` gives that money the
        # same paper trail a card payment gets, and keeps it distinguishable
        # from `payment.*` (gateway) and `reconcile.*` (gateway, after the
        # fact) so nobody later mistakes it for something Worldline confirmed.
        #
        # Nothing overwrites this: reconcile_payments() never considers a
        # registration that is already `paid` or `refunded`, so a manually
        # settled one is not a candidate and survives every subsequent run.
        #
        # The amount depends on which way the money moved, and the two are not
        # the same number. A settlement credits the balance: crediting
        # `reg.amount` would settle the whole fee again on a registration that
        # was already part paid, and would credit it afresh every time the
        # status was toggled back to paid, which a correction routinely does.
        # Against the balance the second toggle credits nothing, because
        # nothing is owed.
        #
        # A reversal is the mirror of that, and the balance is exactly the
        # wrong number for it: a paid registration owes nothing, so recording a
        # refund against the balance recorded a refund of zero. The society
        # handed the money back and its own ledger went on saying it had kept
        # it. A reversal restores what was actually received.
        from ...models import record_payment_event
        from ...models.payment_event import amount_received
        from ...services.invoice import _reg_merchant_reference
        moved = (amount_received(reg.id) if new_status == "refunded"
                 else reg.amount_due)
        record_payment_event(
            transaction_id=reg.transaction_id or "",
            merchant_reference=_reg_merchant_reference(reg),
            registration_id=reg.id,
            event_type=f"manual.{new_status}",
            amount=moved,
            note=(f"{reg.reference}: {old_status} → {new_status}, "
                  f"set by {current_user.email}"),
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
from . import financial     # noqa: F401, E402
