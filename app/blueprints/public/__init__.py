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
        ledger_note = ""
        reg = None
        if result.registration_id:
            reg = db.session.get(Registration, result.registration_id)
            if not reg:
                ledger_note = "no matching registration"
            if reg:
                old_status = reg.status
                old_txn = reg.transaction_id
                reg.transaction_id = result.transaction_id or reg.transaction_id
                reg.last_webhook_event = result.event_type or result.error or "unknown"
                event_type = (result.event_type or "").lower()

                if "refund" in event_type:
                    if result.success:
                        # Settled refund (payment.refunded / refund.refunded).
                        first_refund = reg.status != "refunded"
                        reg.status = "refunded"
                        db.session.commit()
                        if first_refund:
                            try:
                                send_invoice_email(reg)
                            except Exception:
                                current_app.logger.exception("Refund invoice email failed for reg %d", reg.id)
                    else:
                        # Initiated (refund.created) or failed refunds must
                        # not flip the status; failures need human eyes.
                        db.session.commit()
                        if any(w in event_type for w in ("rejected", "cancelled")):
                            _notify_payment_attention(reg, result)
                elif event_type in ("payment.chargebacked",
                                    "payment.chargeback_reversed",
                                    "payment.reversed"):
                    # Disputes are managed in the Merchant Portal; record the
                    # event and alert admins without changing the status.
                    db.session.commit()
                    _notify_payment_attention(reg, result)
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
                        # Already paid or refunded. A redelivered webhook for
                        # the SAME transaction is routine; a successful capture
                        # under a DIFFERENT transaction ID means the member
                        # paid twice — keep the original ID and alert admins.
                        double_payment = (
                            reg.status in ("paid", "refunded")
                            and old_txn and result.transaction_id
                            and result.transaction_id != old_txn)
                        if double_payment:
                            reg.transaction_id = old_txn
                        db.session.commit()
                        if double_payment:
                            _notify_payment_attention(reg, result, reason=(
                                f"A second successful payment "
                                f"({result.transaction_id}) arrived for a "
                                f"registration that is already {reg.status} "
                                f"(original transaction {old_txn}). This is "
                                f"likely a double payment — verify both "
                                f"transactions in the Merchant Portal and "
                                f"refund the duplicate."))
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
                ledger_note = (f"status {old_status} → {reg.status}"
                               if reg.status != old_status
                               else f"status unchanged ({reg.status})")
        elif (result.merchant_reference or "").startswith("test_"):
            _record_test_payment_event(result)
            ledger_note = "admin test payment"
        elif result.merchant_reference:
            # A non-registration, non-test reference — e.g. a manual invoice
            # paid via its durable link (§8). Send the receipt on capture; any
            # other unreferenced event just lands in the ledger below.
            ledger_note = _handle_manual_invoice_event(result)

        if result.event_type:
            from ...models import record_payment_event
            record_payment_event(
                transaction_id=result.transaction_id,
                merchant_reference=result.merchant_reference,
                registration_id=reg.id if reg else None,
                event_type=result.event_type,
                amount=result.amount,
                note=ledger_note,
            )
        return {"status": "ok" if result.success else "ignored"}, 200
    except Exception:
        current_app.logger.exception("Webhook processing failed")
        return {"status": "error", "message": "Payment processing error"}, 500


def _notify_payment_attention(reg, result, reason: str = "") -> None:
    """Audit and email admins about a payment event needing human action:
    a dispute/chargeback, a failed refund, or a suspected double payment."""
    from ...models.user import User
    from ...models.content import get_site_settings
    from ...security import audit
    from ...services.jinja_filters import format_amount
    from ...services.mail import send_mail

    event = result.event_type or "unknown"
    amount = format_amount(result.amount) if result.amount is not None else format_amount(reg.amount)
    user_email = reg.user.email if reg.user else "unknown"
    summary = (f"Registration {reg.id} ({user_email}): {event}, ${amount}, "
               f"transaction {result.transaction_id or reg.transaction_id or 'n/a'}")
    audit.record("financial.payment_attention",
                 target_kind="registration", target_id=str(reg.id),
                 summary=summary)

    site = get_site_settings()
    admins = User.query.filter(User.role_name == "admin",
                               User.deleted_at.is_(None)).all()
    explanation = reason or (
        "Disputes and refund failures are managed in the Worldline "
        "Merchant Portal. Update the registration status manually "
        "in the admin if needed.")
    for admin in admins:
        send_mail(
            to=admin.email,
            subject=f"[{site.site_name}] Payment needs attention: {event}",
            body=(f"A payment event that needs review was received.\n\n"
                  f"Registration: {reg.id} ({user_email})\n"
                  f"Event: {event}\n"
                  f"Amount: ${amount}\n"
                  f"Transaction: {result.transaction_id or reg.transaction_id or 'n/a'}\n"
                  f"Registration status: {reg.status} (unchanged)\n\n"
                  f"{explanation}"),
        )


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


def _handle_manual_invoice_event(result) -> str:
    """Handle a webhook for a manual-invoice reference (durable pay link, §8).

    On a captured payment for a reference that was issued as a manual invoice,
    send the receipt to the stored recipient (idempotent — a redelivered
    capture won't re-send once a document.sent exists). Returns a short ledger
    note. Never raises: a render failure is absorbed by the send layer's §7
    path, and any other error is logged so the webhook still returns 200."""
    from ...models import PaymentEvent

    ref = result.merchant_reference or ""
    evts = PaymentEvent.query.filter_by(merchant_reference=ref).all()
    if not any(e.event_type == "invoice.sent" for e in evts):
        return "unreferenced event"          # not a manual invoice
    if (result.event_type or "").lower() not in ("payment.captured", "payment.paid"):
        return "manual invoice event"
    if any(e.event_type == "document.sent" for e in evts):
        return "manual invoice paid (receipt already sent)"
    try:
        from ...services.invoice import send_manual_invoice_receipt
        send_manual_invoice_receipt(ref, amount_cents=result.amount,
                                    transaction_id=result.transaction_id)
    except Exception:
        current_app.logger.exception("Manual invoice receipt failed for %s", ref)
    return "manual invoice paid — receipt sent"


# ---------------------------------------------------------------------------
# Durable invoice pay link + success page (plan §8)
# ---------------------------------------------------------------------------

def _invoice_events(reference: str):
    """All ledger events for a manual-invoice reference, or None when the
    reference is not a known manual invoice (no `invoice.sent` event)."""
    from ...models import PaymentEvent
    evts = PaymentEvent.query.filter_by(merchant_reference=reference).all()
    if not evts or not any(e.event_type == "invoice.sent" for e in evts):
        return None
    return evts


def _invoice_pay_state(evts) -> str:
    """Best-known payment state of a manual invoice from its ledger events."""
    suffixes = {e.event_type.rsplit(".", 1)[-1] for e in evts}
    if "refunded" in suffixes:
        return "refunded"
    if suffixes & {"captured", "paid"}:
        return "paid"
    if "created" in suffixes:                # checkout.created — awaiting capture
        return "processing"
    return "open"


def _invoice_amount(evts) -> int:
    """The amount owed on a manual invoice — from its `invoice.sent` event."""
    sent = [e.amount for e in evts if e.event_type == "invoice.sent" and e.amount]
    if sent:
        return sent[-1]
    amounts = [e.amount for e in evts if e.amount]
    return amounts[0] if amounts else 0


@public_bp.route("/pay/invoice/<reference>")
def pay_invoice(reference):
    """Durable invoice pay link (§8): mint a fresh hosted checkout for the
    invoice and redirect to Worldline. Rejects unknown/paid/refunded references
    with a generic themed page (no enumeration-friendly detail)."""
    from ...models import record_payment_event
    from ...services.payments import _active_gateway

    spent = _link_budget("invoicelink.mint", reference, _LINK_MINT_LIMIT)
    if spent is not None:
        return spent
    evts = _invoice_events(reference)
    if evts is None or _invoice_pay_state(evts) in ("paid", "refunded"):
        return render_template("public/pay_invoice_message.html",
                               heading="Payment link not available",
                               message=("This payment link is not valid or is no "
                                        "longer available. If you believe this is "
                                        "an error, please contact us."),
                               reference=""), 200

    gateway = _active_gateway()
    site = get_site_settings()
    amount = _invoice_amount(evts)
    if not gateway or not amount:
        return render_template("public/pay_invoice_message.html",
                               heading="Online payment unavailable",
                               message=("Online card payment is currently "
                                        "unavailable. Please pay by bank transfer "
                                        "using the EFT details on your invoice, "
                                        "quoting the reference below."),
                               reference=reference), 200

    result = gateway.create_invoice_checkout(
        amount=amount, reference=reference,
        return_url=url_for("public.pay_invoice_result", reference=reference,
                           _external=True),
        currency=(site.currency_code or "AUD").upper())
    if result.error or not result.redirect_url:
        return render_template("public/pay_invoice_message.html",
                               heading="Online payment unavailable",
                               message=("We couldn't start the online payment just "
                                        "now. Please try again shortly, or pay by "
                                        "bank transfer using the EFT details on "
                                        "your invoice."),
                               reference=reference), 200

    record_payment_event(
        transaction_id=result.payment_id, merchant_reference=reference,
        event_type="checkout.created", amount=amount,
        note="hosted checkout created from durable invoice link")
    return redirect(result.redirect_url)


@public_bp.route("/pay/invoice/<reference>/result")
def pay_invoice_result(reference):
    """Return-from-checkout page for a durable invoice payment (§8): reflects
    the ledger state like the member pay_result pattern.

    Rate limited like its sibling, and answering unknown references with the
    same page the pay route uses: an invoice reference carries only four hex
    characters of entropy, so an endpoint that says "no such invoice" quickly
    and distinctly is an index of the invoice book.
    """
    spent = _link_budget("invoicelink.view", reference, _LINK_VIEW_LIMIT)
    if spent is not None:
        return spent
    evts = _invoice_events(reference)
    if evts is None:
        return _pay_link_unavailable(
            "Payment link not available",
            "This payment link is not valid or is no longer available. If you "
            "believe this is an error, please contact us.")
    return render_template("public/pay_invoice_result.html",
                           reference=reference, state=_invoice_pay_state(evts),
                           amount=_invoice_amount(evts))


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


# ---------------------------------------------------------------------------
# Durable registration pay link
#
# The registration equivalent of the invoice link above, and public for the
# same reason: the person who registers is often not the person who pays.
# An academic forwards the email to a grant administrator or a finance office
# who has no account here, and a login wall simply stops the money arriving.
#
# The capability is the token, not a session — 32 bytes of randomness, so the
# sequential id that made the old /pay/<reg_id> route safe only behind a login
# is never exposed. Rate-limited and answered with the same generic page as the
# invoice link, so an unknown token reveals nothing an attacker could iterate.
# ---------------------------------------------------------------------------

# A pay link is used by one or two people. These budgets are generous for a
# finance office retrying a card and still bound how much noise one link can
# make — minting checkouts is the expensive half, so it is the tighter of the
# two. Keyed on the link, never the IP: see app/models/rate_limit.py.
_LINK_VIEW_LIMIT = 20
_LINK_MINT_LIMIT = 5
_LINK_WINDOW = 3600


def _link_budget(scope: str, resource: str, limit: int):
    """None when the link may proceed, or the page to answer with instead."""
    from ...models.rate_limit import allow

    if allow(scope, resource, limit=limit, per_seconds=_LINK_WINDOW):
        return None
    return _pay_link_unavailable(
        "Too many attempts",
        "This payment link has been opened too many times in the last hour. "
        "Please wait a little and try again, or contact us to arrange "
        "payment.")


def _registration_for_token(token: str):
    """The live registration a pay token belongs to, or None."""
    from ...models import Registration

    if not token:
        return None
    return (Registration.query
            .filter_by(pay_token=token)
            .filter(Registration.deleted_at.is_(None))
            .first())


def _pay_link_unavailable(heading: str, message: str, reference: str = ""):
    return render_template("public/pay_invoice_message.html", heading=heading,
                           message=message, reference=reference), 200


def _settled_message(reg):
    """The page a settled registration answers with, or None if still payable.

    Checked before a checkout is ever minted: a forwarded link outlives the
    payment, and two people holding the same link must not each be able to
    start a fresh session against a registration that is already paid.
    """
    if reg.status == "paid":
        return _pay_link_unavailable(
            "Already paid",
            "This registration has already been paid. Nothing further is due, "
            "and no payment has been taken just now.",
            reg.reference)
    if reg.status == "refunded":
        return _pay_link_unavailable(
            "Already refunded",
            "This registration has been refunded, so it can no longer be paid "
            "through this link.", reg.reference)
    if reg.status == "cancelled":
        return _pay_link_unavailable(
            "Registration cancelled",
            "This registration has been cancelled, so it can no longer be "
            "paid.", reg.reference)
    return None


@public_bp.route("/pay/registration/<token>")
def pay_registration(token):
    """Durable registration pay link — the page, not the gateway."""
    spent = _link_budget("paylink.view", token, _LINK_VIEW_LIMIT)
    if spent is not None:
        return spent
    reg = _registration_for_token(token)
    if reg is None:
        return _pay_link_unavailable(
            "Payment link not available",
            "This payment link is not valid or is no longer available. If you "
            "believe this is an error, please contact us.")

    settled = _settled_message(reg)
    if settled is not None:
        return settled
    if reg.status == "processing":
        return redirect(url_for("public.pay_registration_result", token=token))

    from ...services.payments import payments_open_to_members, sandbox_mode
    return render_template("member/pay.html", reg=reg,
                           site=get_site_settings(),
                           gateway_available=payments_open_to_members(),
                           testing=False, sandbox=sandbox_mode(),
                           pay_token=token)


@public_bp.route("/pay/registration/<token>/checkout", methods=["POST"])
def pay_registration_checkout(token):
    """Mint the hosted checkout for a durable registration link.

    Re-checks the settled states rather than trusting the page that produced
    this POST: the page may have been rendered before somebody else paid.
    """
    from ...services.payments import initiate_payment, payments_open_to_members

    spent = _link_budget("paylink.mint", token, _LINK_MINT_LIMIT)
    if spent is not None:
        return spent
    reg = _registration_for_token(token)
    if reg is None:
        return _pay_link_unavailable(
            "Payment link not available",
            "This payment link is not valid or is no longer available.")

    settled = _settled_message(reg)
    if settled is not None:
        return settled
    if not payments_open_to_members():
        return _pay_link_unavailable(
            "Online payment unavailable",
            "Online card payment is currently unavailable. Please contact us "
            "to arrange payment, quoting the reference below.", reg.reference)

    redirect_url = initiate_payment(
        reg, return_url=url_for("public.pay_registration_result", token=token,
                                _external=True))
    if not redirect_url:
        return _pay_link_unavailable(
            "Online payment unavailable",
            "We couldn't start the online payment just now. Please try again "
            "shortly, or contact us to arrange payment.", reg.reference)
    return redirect(redirect_url)


@public_bp.route("/pay/registration/<token>/result")
def pay_registration_result(token):
    """Return-from-checkout page for a durable registration link."""
    spent = _link_budget("paylink.view", token, _LINK_VIEW_LIMIT)
    if spent is not None:
        return spent
    reg = _registration_for_token(token)
    if reg is None:
        return _pay_link_unavailable(
            "Payment link not available",
            "This payment link is not available.")
    return render_template("member/pay_result.html", reg=reg,
                           site=get_site_settings(), pay_token=token,
                           already_complete=reg.status in ("paid", "refunded",
                                                           "processing"))
