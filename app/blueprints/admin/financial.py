"""Admin → Financial: payment provider configuration, invoice templates,
and API key expiry management."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from . import admin_bp
from ...extensions import db
from ...models import (
    OTPCode, PaymentGatewayConfig, InvoiceTemplate,
    get_payment_gateway_config, get_active_payment_gateway,
    get_invoice_template,
)
from ...security import audit, requires_permission
from ...services.mail import send_mail


@admin_bp.route("/financial")
@requires_permission("financial.manage")
def financial():
    anzw_cfg = get_payment_gateway_config("anz_worldline")
    if not anzw_cfg:
        anzw_cfg = PaymentGatewayConfig(provider="anz_worldline")
        db.session.add(anzw_cfg)
        db.session.commit()

    invoice_tpl = get_invoice_template()

    return render_template(
        "admin/financial.html",
        config=anzw_cfg,
        invoice=invoice_tpl,
    )


@admin_bp.route("/financial/anz_worldline/edit", methods=["GET", "POST"])
@requires_permission("financial.manage")
def financial_edit():
    cfg = get_payment_gateway_config("anz_worldline")
    if not cfg:
        cfg = PaymentGatewayConfig(provider="anz_worldline")
        db.session.add(cfg)
        db.session.commit()

    if request.method == "POST":
        was_enabled = cfg.is_enabled
        was_live = was_enabled and not cfg.is_test_mode
        cfg.is_enabled = request.form.get("is_enabled") == "1"
        # Live mode is only ever reached through the OTP-confirmed toggle
        # on an already-enabled gateway: disabling resets to sandbox, and
        # enabling from disabled always lands in sandbox.
        if not cfg.is_test_mode and (not cfg.is_enabled or not was_enabled):
            cfg.is_test_mode = True
            if was_live:
                flash("Gateway disabled — sandbox mode re-engaged. Going live "
                      "again will require OTP confirmation.", "warning")
        cfg.merchant_id = (request.form.get("merchant_id") or "").strip()
        cfg.api_key_id = (request.form.get("api_key_id") or "").strip()

        api_secret = (request.form.get("api_secret") or "").strip()
        if api_secret and not api_secret.startswith("••••"):
            cfg.set_api_secret(api_secret)

        cfg.webhooks_key_id = (request.form.get("webhooks_key_id") or "").strip()

        webhooks_secret = (request.form.get("webhooks_secret") or "").strip()
        if webhooks_secret and not webhooks_secret.startswith("••••"):
            cfg.set_webhooks_secret(webhooks_secret)

        is_new_key = request.form.get("is_new_key") == "1"
        if is_new_key:
            cfg.api_key_set_at = datetime.utcnow()
            cfg.expiry_warnings_sent = None

        db.session.commit()
        audit.record("financial.gateway_updated",
                     target_kind="payment_gateway_config", target_id=str(cfg.id),
                     summary=f"ANZ Worldline config updated by {current_user.email}")
        flash("Payment gateway settings saved.", "success")
        return redirect(url_for("admin.financial"))

    return render_template("admin/financial_edit.html", config=cfg)


@admin_bp.route("/financial/anz_worldline/test", methods=["POST"])
@requires_permission("financial.manage")
def financial_test():
    from ...services.gateways.anz_worldline import ANZWorldlineGateway

    cfg = get_payment_gateway_config("anz_worldline")
    if not cfg:
        flash("No gateway configured.", "error")
        return redirect(url_for("admin.financial"))

    gateway = ANZWorldlineGateway(cfg)
    result = gateway.test_connection()

    audit.record("financial.test_connection",
                 target_kind="payment_gateway_config", target_id=str(cfg.id),
                 summary=f"Test connection: {'OK' if result.success else 'FAILED'}")

    if result.success:
        flash(f"Connection successful: {result.message}", "success")
    else:
        flash(f"Connection failed: {result.message}", "error")

    return redirect(url_for("admin.financial"))


@admin_bp.route("/financial/anz_worldline/toggle-sandbox-request", methods=["POST"])
@requires_permission("financial.manage")
def financial_toggle_sandbox_request():
    cfg = get_payment_gateway_config("anz_worldline")
    if not cfg:
        flash("No gateway configured.", "error")
        return redirect(url_for("admin.financial"))

    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    ok = send_mail(
        to=current_user.email,
        subject="Confirm sandbox mode change",
        body=(f"You requested to {'enable' if cfg.is_test_mode else 'disable'} "
              f"sandbox mode for ANZ Worldline payments.\n\n"
              f"Confirmation code: {code}\n\n"
              f"This code expires in {ttl // 60} minutes. "
              f"If you didn't request this, ignore the email."),
    )
    if not ok:
        flash("Failed to send confirmation email. Please try again.", "error")
        return redirect(url_for("admin.financial"))

    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="financial_sandbox_toggle",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    flash("A confirmation code has been sent to your email.", "success")
    return redirect(url_for("admin.financial_toggle_sandbox_confirm", provider="anz_worldline"))


@admin_bp.route("/financial/<provider>/toggle-sandbox-confirm", methods=["GET", "POST"])
@requires_permission("financial.manage")
def financial_toggle_sandbox_confirm(provider):
    cfg = get_payment_gateway_config(provider)
    if not cfg:
        flash("No gateway configured.", "error")
        return redirect(url_for("admin.financial"))

    if request.method == "POST":
        entered = (request.form.get("code") or "").strip().replace(" ", "")
        otp = (OTPCode.query
               .filter_by(email=current_user.email.lower(),
                          code=entered,
                          purpose="financial_sandbox_toggle",
                          consumed_at=None)
               .order_by(OTPCode.id.desc())
               .first())
        if not (otp and otp.is_valid()):
            flash("That code didn't match, or it has expired.", "error")
            return render_template("admin/financial_sandbox_confirm.html", config=cfg)

        otp.consumed_at = datetime.utcnow()
        cfg.is_test_mode = not cfg.is_test_mode
        db.session.commit()

        new_mode = "sandbox" if cfg.is_test_mode else "live"
        audit.record("financial.sandbox_toggled",
                     target_kind="payment_gateway_config", target_id=str(cfg.id),
                     summary=f"Sandbox mode {'enabled' if cfg.is_test_mode else 'disabled'} by {current_user.email}")
        flash(f"Payment mode changed to {new_mode}.", "success")
        return redirect(url_for("admin.financial"))

    return render_template("admin/financial_sandbox_confirm.html", config=cfg)


@admin_bp.route("/financial/invoice", methods=["GET", "POST"])
@requires_permission("financial.manage")
def financial_invoice():
    tpl = get_invoice_template()

    if request.method == "POST":
        tpl.subject = (request.form.get("subject") or "").strip()
        tpl.body_text = (request.form.get("body_text") or "").strip()
        tpl.body_html = (request.form.get("body_html") or "").strip() or None
        tpl.from_name = (request.form.get("from_name") or "").strip()
        tpl.from_email = (request.form.get("from_email") or "").strip()
        tpl.footer_text = (request.form.get("footer_text") or "").strip()
        db.session.commit()
        audit.record("financial.invoice_template_updated",
                     target_kind="invoice_template", target_id=str(tpl.id),
                     summary="Invoice template updated")
        flash("Invoice template saved.", "success")
        return redirect(url_for("admin.financial"))

    return render_template("admin/financial_invoice.html", invoice=tpl)


@admin_bp.route("/financial/anz_worldline/test-payment", methods=["POST"])
@requires_permission("financial.manage")
def financial_test_payment():
    """Start a small real checkout so the gateway can be verified end-to-end
    without touching any registration. Confirmation arrives via webhook."""
    from ...services.gateways.anz_worldline import ANZWorldlineGateway
    from ...services.jinja_filters import format_amount, parse_cents

    cfg = get_payment_gateway_config("anz_worldline")
    if not cfg or not cfg.is_enabled:
        flash("Enable the gateway before starting a test payment.", "error")
        return redirect(url_for("admin.financial"))

    try:
        amount = parse_cents(request.form.get("amount") or "1.00")
    except ValueError:
        flash("Enter a valid test amount, e.g. 1.00.", "error")
        return redirect(url_for("admin.financial"))
    if not (1 <= amount <= 1000):
        flash("Test amount must be between $0.01 and $10.00.", "error")
        return redirect(url_for("admin.financial"))

    reference = f"test_{secrets.token_hex(4)}"
    result = ANZWorldlineGateway(cfg).create_test_checkout(amount, reference)

    audit.record("financial.test_payment_started",
                 target_kind="payment_gateway_config", target_id=str(cfg.id),
                 summary=(f"Test payment {reference} for ${format_amount(amount)} "
                          f"started by {current_user.email} "
                          f"({'sandbox' if cfg.is_test_mode else 'LIVE'})"))

    if result.error or not result.redirect_url:
        flash(f"Could not start test payment: {result.error or 'no redirect URL'}",
              "error")
        return redirect(url_for("admin.financial"))

    from ...models import record_payment_event
    record_payment_event(
        transaction_id=result.payment_id,
        merchant_reference=reference,
        event_type="checkout.created",
        amount=amount,
        note=f"test payment started by {current_user.email}",
    )
    return redirect(result.redirect_url)


@admin_bp.route("/financial/test-invoice", methods=["POST"])
@requires_permission("financial.manage")
def financial_test_invoice():
    """Send the invoice template, rendered with sample data, to a chosen address."""
    from ...services.invoice import send_test_invoice

    to = (request.form.get("email") or "").strip() or current_user.email
    if "@" not in to:
        flash("Enter a valid email address for the test invoice.", "error")
        return redirect(url_for("admin.financial"))

    ok = send_test_invoice(to)
    audit.record("financial.test_invoice_sent",
                 target_kind="invoice_template", target_id="1",
                 summary=f"Test invoice sent to {to} by {current_user.email}")
    if ok:
        flash(f"Test invoice sent to {to}.", "success")
    else:
        flash("Failed to send test invoice — check the mail settings.", "error")
    return redirect(url_for("admin.financial"))


@admin_bp.route("/financial/send-invoice", methods=["GET", "POST"])
@requires_permission("financial.manage")
def financial_send_invoice():
    """Send a templated invoice for an agreed amount to chosen recipients
    (e.g. sponsors). Email only — no payment link is created."""
    from ...services.invoice import send_manual_invoice
    from ...services.jinja_filters import format_amount, parse_cents

    suggested_ref = f"INV-{datetime.utcnow().strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"

    if request.method == "POST":
        to = (request.form.get("to") or "").strip()
        cc_raw = (request.form.get("cc") or "").replace(";", ",")
        cc = [a.strip() for a in cc_raw.split(",") if a.strip()]
        recipient_name = (request.form.get("recipient_name") or "").strip()
        description = (request.form.get("description") or "").strip()
        item = (request.form.get("item") or "").strip()
        reference = (request.form.get("reference") or "").strip() or suggested_ref
        period = (request.form.get("period") or "").strip()
        subject_override = (request.form.get("subject") or "").strip()

        errors = []
        if "@" not in to:
            errors.append("Enter a valid recipient email.")
        errors += [f"Invalid CC address: {a}" for a in cc if "@" not in a]
        if not description:
            errors.append("Describe what the invoice is for.")
        try:
            amount = parse_cents(request.form.get("amount") or "")
        except ValueError:
            amount = 0
        if amount <= 0:
            errors.append("Enter a valid amount, e.g. 500.00.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/financial_send_invoice.html",
                                   form=request.form, suggested_ref=suggested_ref)

        ok = send_manual_invoice(
            to, cc=cc or None, recipient_name=recipient_name,
            description=description, item=item, amount_cents=amount,
            reference=reference, period=period,
            subject_override=subject_override,
        )
        audit.record("financial.invoice_sent",
                     target_kind="invoice", target_id=reference,
                     summary=(f"Invoice {reference} for ${format_amount(amount)} "
                              f"sent to {to}"
                              f"{' (cc ' + ', '.join(cc) + ')' if cc else ''}"
                              f" by {current_user.email}"))
        if ok:
            from ...models import record_payment_event
            record_payment_event(
                merchant_reference=reference,
                event_type="invoice.sent",
                amount=amount,
                note=(f"to {to}"
                      f"{' (cc ' + ', '.join(cc) + ')' if cc else ''}"
                      f" by {current_user.email}"),
            )
            flash(f"Invoice {reference} sent to {to}"
                  f"{' (cc ' + ', '.join(cc) + ')' if cc else ''}.", "success")
            return redirect(url_for("admin.financial_send_invoice"))
        flash("Failed to send the invoice — check the mail settings.", "error")
        return render_template("admin/financial_send_invoice.html",
                               form=request.form, suggested_ref=suggested_ref)

    return render_template("admin/financial_send_invoice.html",
                           form={}, suggested_ref=suggested_ref)


@admin_bp.route("/financial/member-payments", methods=["POST"])
@requires_permission("financial.manage")
def financial_member_payments():
    """Open or close online payments to members — the final gate in front
    of the checkout. Opening requires the gateway to be enabled and live."""
    from ...models.content import get_site_settings

    site = get_site_settings()
    action = (request.form.get("action") or "").strip()

    if action == "open":
        cfg = get_payment_gateway_config("anz_worldline")
        if not cfg or not cfg.is_enabled:
            flash("Cannot open member payments: the gateway is not enabled.", "error")
            return redirect(url_for("admin.financial"))
        if cfg.is_test_mode:
            flash("Cannot open member payments while the gateway is in sandbox "
                  "mode — members would be sent to the test environment.", "error")
            return redirect(url_for("admin.financial"))
        site.payment_portal_enabled = True
    elif action == "close":
        site.payment_portal_enabled = False
    else:
        flash("Unknown action.", "error")
        return redirect(url_for("admin.financial"))

    db.session.commit()
    state = "opened" if site.payment_portal_enabled else "closed"
    audit.record("financial.member_payments_toggled",
                 target_kind="site_settings", target_id="1",
                 summary=f"Member payments {state} by {current_user.email}")
    flash(f"Member payments {state}.", "success")
    return redirect(url_for("admin.financial"))


@admin_bp.route("/financial/transactions")
@requires_permission("financial.manage")
def financial_transactions():
    """Financial event ledger, grouped per transaction and searchable."""
    from ...models import PaymentEvent, Registration, User

    q = (request.args.get("q") or "").strip()
    query = (PaymentEvent.query
             .outerjoin(Registration, PaymentEvent.registration_id == Registration.id)
             .outerjoin(User, Registration.user_id == User.id))
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            PaymentEvent.transaction_id.ilike(like),
            PaymentEvent.merchant_reference.ilike(like),
            PaymentEvent.event_type.ilike(like),
            PaymentEvent.note.ilike(like),
            User.email.ilike(like),
        ))
    events = (query.order_by(PaymentEvent.created_at.desc(),
                             PaymentEvent.id.desc())
              .limit(500).all())

    groups: dict[str, list] = {}
    for e in events:
        groups.setdefault(e.group_key, []).append(e)

    return render_template("admin/financial_transactions.html",
                           groups=groups, q=q, event_count=len(events))
