"""Public, unauthenticated routes: home, conferences, committee, contact,
custom pages, served uploads.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    send_from_directory, session, url_for,
)
from flask_login import current_user

from ...extensions import csrf, db, limiter
from ...models import (
    Announcement, CommitteeMember, Conference, OTPCode, Page, PastBoard, User,
    Abstract, SPEAKER_STATUSES,
    get_site_settings,
)
from ...services.mail import send_mail
from ...services.citations import fetch_metadata, format_reference_compact


public_bp = Blueprint("public", __name__)


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@public_bp.route("/")
def home():
    today = date.today()
    featured = (
        Conference.query
        .filter(Conference.deleted_at.is_(None), Conference.is_featured.is_(True))
        .order_by(Conference.start_date)
        .first()
    )
    upcoming = (
        Conference.query
        .filter(
            Conference.deleted_at.is_(None),
            Conference.is_draft.is_(False),
            Conference.end_date >= today,
        )
        .order_by(Conference.start_date)
        .all()
    )
    announcements = (
        Announcement.query
        .filter(Announcement.deleted_at.is_(None))
        .order_by(Announcement.pinned.desc(), Announcement.published_at.desc())
        .limit(6)
        .all()
    )
    if featured and featured.auto_reopen():
        db.session.commit()
    return render_template(
        "public/home.html",
        featured=featured,
        upcoming=upcoming,
        announcements=announcements,
    )


# ---------------------------------------------------------------------------
# Conferences
# ---------------------------------------------------------------------------

@public_bp.route("/conferences")
def conferences():
    items = (
        Conference.query
        .filter(Conference.deleted_at.is_(None), Conference.is_draft.is_(False))
        .order_by(Conference.start_date.desc())
        .all()
    )
    return render_template("public/conferences.html", conferences=items)


@public_bp.route("/conferences/<slug>")
def conference_detail(slug):
    c = (Conference.query
         .filter_by(slug=slug)
         .filter(Conference.deleted_at.is_(None))
         .first_or_404())
    if c.is_draft and not (current_user.is_authenticated
                            and current_user.has_permission("conf.view_drafts")):
        abort(404)
    if c.auto_reopen():
        db.session.commit()
    speakers = sorted(
        Abstract.query
        .filter_by(conference_id=c.id)
        .filter(Abstract.status.in_(SPEAKER_STATUSES))
        .filter(Abstract.deleted_at.is_(None))
        .all(),
        key=lambda a: (a.speaker_sort_key, a.created_at),
    )
    return render_template("public/conference_detail.html", c=c, speakers=speakers)


# ---------------------------------------------------------------------------
# Committee
# ---------------------------------------------------------------------------

@public_bp.route("/committee")
def committee():
    items = CommitteeMember.visible_in_order()
    past_boards = (
        PastBoard.query
        .order_by(PastBoard.display_order.desc())
        .all()
    )
    return render_template("public/committee.html", items=items, past_boards=past_boards)


# ---------------------------------------------------------------------------
# Custom Markdown pages (About, Privacy, Terms, Code of Conduct, custom)
# ---------------------------------------------------------------------------

@public_bp.route("/p/<slug>")
def page(slug):
    p = (Page.query
         .filter_by(slug=slug, published=True)
         .filter(Page.deleted_at.is_(None))
         .first_or_404())
    return render_template("public/page.html", page=p)


# ---------------------------------------------------------------------------
# Contact form
# ---------------------------------------------------------------------------

@public_bp.route("/contact", methods=["GET", "POST"])
@limiter.limit("5 per hour;2 per minute", methods=["POST"])
def contact():
    from ...models.committee import CommitteeMember

    admins = (
        User.query
        .filter(User.role_name == "admin",
                User.deleted_at.is_(None),
                User.full_name.isnot(None),
                User.full_name != "")
        .order_by(User.full_name)
        .all()
    )
    contactable_committee = (
        User.query
        .join(CommitteeMember, CommitteeMember.user_id == User.id)
        .filter(User.role_name == "committee",
                User.deleted_at.is_(None),
                CommitteeMember.is_contactable.is_(True))
        .options(db.joinedload(User.committee_profile))
        .order_by(User.full_name)
        .all()
    )
    recipients = admins + contactable_committee
    if request.method == "POST":
        # Honeypot — if a bot fills `website`, silently no-op success.
        if request.form.get("confirm_human", "").strip():
            flash("Message sent.", "success")
            return redirect(url_for("public.contact"))

        try:
            rid = int(request.form.get("recipient_id") or "")
        except ValueError:
            rid = 0
        target = next((u for u in recipients if u.id == rid), None)
        sender_name = (request.form.get("name") or "").strip()
        sender_email = (request.form.get("email") or "").strip()
        user_subject = (request.form.get("subject") or "").strip()
        message = (request.form.get("message") or "").strip()

        if not (target and sender_name and sender_email and message):
            flash("Please fill in every field.", "error")
            return render_template("public/contact.html",
                                   recipients=recipients, form=request.form)

        # Store form data in session and issue OTP to verify email ownership.
        session["contact_form"] = {
            "name": sender_name,
            "email": sender_email,
            "subject": user_subject,
            "message": message,
            "recipient_id": rid,
        }
        code = f"{secrets.randbelow(1_000_000):06d}"
        ttl = current_app.config["OTP_TTL_SECONDS"]
        site_name = get_site_settings().site_name
        ok = send_mail(
            to=sender_email,
            subject=f"Your {site_name} contact form verification code",
            body=(f"Your one-time verification code is: {code}\n\n"
                  f"It expires in {ttl // 60} minutes. "
                  f"If you didn't request this, you can safely ignore this email."),
        )
        if not ok:
            session.pop("contact_form", None)
            flash("Failed to send verification code. Please try again.", "error")
            return render_template("public/contact.html",
                                   recipients=recipients, form=request.form)
        db.session.add(OTPCode(
            email=sender_email.lower(),
            code=code,
            purpose="contact_form",
            expires_at=datetime.utcnow() + timedelta(seconds=ttl),
            ip=request.remote_addr,
        ))
        db.session.commit()
        flash("A verification code has been sent to your email.", "success")
        return redirect(url_for("public.contact_verify"))

    return render_template("public/contact.html", recipients=recipients, form={})


@public_bp.route("/contact/verify", methods=["GET", "POST"])
@limiter.limit("10 per hour;4 per minute", methods=["POST"])
def contact_verify():
    data = session.get("contact_form")
    if not data:
        return redirect(url_for("public.contact"))

    recipient = User.query.get(data.get("recipient_id"))
    if not recipient or recipient.deleted_at:
        session.pop("contact_form", None)
        return redirect(url_for("public.contact"))

    if request.method == "POST":
        entered = (request.form.get("code") or "").strip().replace(" ", "")
        otp = (OTPCode.query
               .filter_by(email=data["email"].lower(),
                          code=entered,
                          purpose="contact_form",
                          consumed_at=None)
               .order_by(OTPCode.id.desc())
               .first())
        if not (otp and otp.is_valid()):
            flash("That code didn't match, or it has expired.", "error")
            return render_template("public/contact_verify.html", data=data,
                                   recipient=recipient)

        otp.consumed_at = datetime.utcnow()
        db.session.commit()

        sender_name = data["name"]
        sender_email = data["email"]
        user_subject = data.get("subject") or ""
        message = data["message"]

        body = (f"From: {sender_name} <{sender_email}>\n"
                f"Subject: {user_subject}\n\n{message}\n") if user_subject else (
                f"From: {sender_name} <{sender_email}>\n"
                f"Sent via the contact form.\n\n{message}\n")
        site_name = get_site_settings().site_name
        ok = send_mail(recipient.email,
                       f"{site_name} Contact Form — {sender_name}", body,
                       sender_name=f"{site_name} Contact Form",
                       reply_to=f"{sender_name} <{sender_email}>")

        # Send confirmation copy to submitter.
        copy_body = (
            f"Thank you for contacting {site_name}. "
            f"Your message was sent to {recipient.full_name}.\n\n"
            f"Here is a copy for your records:\n\n"
            f"---\n\n{message}"
        )
        send_mail(sender_email, f"{site_name} — we received your message", copy_body,
                  sender_name=f"{site_name} Contact Form")

        session.pop("contact_form", None)

        if ok:
            flash(f"Message sent to {recipient.full_name}.", "success")
        else:
            flash("Message could not be sent. Please try again later.", "error")
        return redirect(url_for("public.contact"))

    return render_template("public/contact_verify.html", data=data,
                           recipient=recipient)


@public_bp.route("/contact/resend", methods=["POST"])
@limiter.limit("4 per hour;2 per minute")
def contact_resend():
    data = session.get("contact_form")
    if not data:
        return redirect(url_for("public.contact"))

    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    site_name = get_site_settings().site_name
    ok = send_mail(
        to=data["email"],
        subject=f"Your {site_name} contact form verification code",
        body=(f"A new verification code has been requested.\n\n"
              f"Your one-time verification code is: {code}\n\n"
              f"It expires in {ttl // 60} minutes. "
              f"If you didn't request this, you can safely ignore this email."),
    )
    if not ok:
        flash("Failed to send verification code. Please try again.", "error")
        return redirect(url_for("public.contact_verify"))
    db.session.add(OTPCode(
        email=data["email"].lower(),
        code=code,
        purpose="contact_form",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    flash("A new code has been sent to your email.", "success")
    return redirect(url_for("public.contact_verify"))


# ---------------------------------------------------------------------------
# Served uploads — keep behind sensible checks.
# ---------------------------------------------------------------------------

@public_bp.route("/uploads/site/<path:name>")
def site_upload(name):
    """Site-wide images (logo, favicon, hero, OG) — always public."""
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "site"
    return send_from_directory(folder, name)


@public_bp.route("/uploads/committee/<path:name>")
def committee_upload(name):
    """Committee portraits — public."""
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "committee"
    return send_from_directory(folder, name)


@public_bp.route("/uploads/conferences/<path:name>")
def conference_upload(name):
    """Conference assets (hero, booklet) — public."""
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "conferences"
    return send_from_directory(folder, name)


@public_bp.route("/uploads/sponsors/<path:name>")
def sponsor_upload(name):
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "sponsors"
    return send_from_directory(folder, name)


# ---------------------------------------------------------------------------
# Abstract public view (linked from speaker cards)
# ---------------------------------------------------------------------------

@public_bp.route("/abstracts/<int:aid>")
def abstract_view(aid):
    a = Abstract.query.get_or_404(aid)
    if not a.status or a.status not in SPEAKER_STATUSES:
        abort(404)

    refs_with_meta: list[dict] = []
    for ref in (a.references or []):
        meta = fetch_metadata(ref["doi"])
        if meta:
            refs_with_meta.append({
                "key": ref["key"],
                "doi": ref["doi"],
                "citation": format_reference_compact(meta),
            })
        else:
            refs_with_meta.append({
                "key": ref["key"],
                "doi": ref["doi"],
                "citation": ref["doi"],
            })

    return render_template("public/abstract.html", a=a, refs_with_meta=refs_with_meta)


@public_bp.route("/uploads/abstracts/<path:name>")
def abstract_upload(name):
    """Abstract profile pictures — public (used by speaker cards)."""
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "abstracts"
    return send_from_directory(folder, name)


@public_bp.route("/favicon.ico")
def favicon():
    from ...models import get_site_settings
    s = get_site_settings()
    if s.favicon_filename:
        return redirect(url_for("public.site_upload", name=s.favicon_filename))
    abort(404)


@public_bp.route("/payments/webhook", methods=["POST"])
@csrf.exempt
@limiter.exempt
def payment_webhook():
    """Receive payment provider webhooks. Provider selected from DB config."""
    from ...models import Registration
    from ...services.payments import _active_gateway
    from ...services.invoice import send_invoice_email

    g = _active_gateway()
    if not g:
        return {"status": "no gateway configured"}, 200

    try:
        result = g.verify_webhook(request.data, dict(request.headers))
        if result.registration_id:
            reg = db.session.get(Registration, result.registration_id)
            if reg:
                reg.transaction_id = result.transaction_id or reg.transaction_id
                reg.last_webhook_event = result.event_type or result.error or "unknown"
                event_type = (result.event_type or "").lower()

                if "refund" in event_type:
                    first_refund = reg.status != "refunded"
                    reg.status = "refunded"
                    db.session.commit()
                    if first_refund:
                        try:
                            send_invoice_email(reg)
                        except Exception:
                            current_app.logger.exception("Refund invoice email failed for reg %d", reg.id)
                elif result.success:
                    if reg.status in ("pending", "processing", "failed"):
                        reg.status = "paid"
                        db.session.commit()
                        current_app.logger.info(
                            "Payment webhook: reg %d marked paid (%s)",
                            result.registration_id, result.transaction_id)
                        try:
                            send_invoice_email(reg)
                        except Exception:
                            current_app.logger.exception("Invoice email failed for reg %d", reg.id)
                    else:
                        db.session.commit()
                elif event_type in ("payment.rejected", "payment.rejected_capture", "payment.cancelled"):
                    # Never downgrade a completed payment on a stale or
                    # out-of-order failure event.
                    if reg.status in ("pending", "processing"):
                        reg.status = "cancelled" if event_type == "payment.cancelled" else "failed"
                    db.session.commit()
                elif event_type in ("payment.pending_capture", "payment.capture_requested"):
                    if reg.status == "pending":
                        reg.status = "processing"
                    db.session.commit()
                else:
                    db.session.commit()
        elif (result.merchant_reference or "").startswith("test_"):
            _record_test_payment_event(result)
        return {"status": "ok" if result.success else "ignored"}, 200
    except Exception:
        current_app.logger.exception("Webhook processing failed")
        return {"status": "error", "message": "Payment processing error"}, 500


def _record_test_payment_event(result) -> None:
    """Audit an admin-initiated test payment event; email admins when it
    settles. The webhook is the only reliable confirmation channel when the
    portal account can't view transactions."""
    from ...models.user import User
    from ...models.content import get_site_settings
    from ...security import audit
    from ...services.jinja_filters import format_amount
    from ...services.mail import send_mail

    event = result.event_type or "unknown"
    amount = format_amount(result.amount) if result.amount is not None else "?"
    summary = (f"Test payment {result.merchant_reference}: {event}, ${amount}, "
               f"transaction {result.transaction_id or 'n/a'}")
    audit.record("financial.test_payment_event",
                 target_kind="payment_gateway",
                 target_id=result.merchant_reference,
                 summary=summary)

    terminal = result.success or any(
        w in event for w in ("rejected", "cancelled", "refunded"))
    if not terminal:
        return

    site = get_site_settings()
    admins = User.query.filter(User.role_name == "admin",
                               User.deleted_at.is_(None)).all()
    for admin in admins:
        send_mail(
            to=admin.email,
            subject=f"[{site.site_name}] Gateway test payment: {event}",
            body=(f"A payment gateway test event was received.\n\n"
                  f"Reference: {result.merchant_reference}\n"
                  f"Event: {event}\n"
                  f"Amount: ${amount}\n"
                  f"Transaction: {result.transaction_id or 'n/a'}\n\n"
                  f"If this was a live test charge, remember to refund it from "
                  f"the Worldline Merchant Portal."),
        )


@public_bp.route("/.well-known/security.txt")
def security_txt():
    return ("Contact: https://github.com/Grimblewald/Conventus/issues\n"
            "Expires: 2027-01-01T00:00:00.000Z\n"
            "Preferred-Languages: en\n"
            "Canonical: https://github.com/Grimblewald/Conventus/security\n",
            200, {"Content-Type": "text/plain; charset=utf-8"})


@public_bp.route("/dev/reload")
def dev_reload():
    if not current_app.debug:
        abort(404)
    Path(__file__).parent.parent.parent.parent.joinpath("wsgi.py").touch()
    return "ok — reload triggered"
