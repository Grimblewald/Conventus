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
        cfg.is_enabled = request.form.get("is_enabled") == "1"
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
