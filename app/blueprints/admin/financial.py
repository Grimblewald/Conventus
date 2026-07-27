"""Admin → Financial: payment provider configuration, invoice templates,
and API key expiry management."""
from __future__ import annotations

import os
import secrets
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    current_app, flash, redirect, render_template, request, send_file, url_for,
)
from flask_login import current_user

from . import admin_bp
from ...extensions import db
from ...models import (
    OTPCode, PaymentGatewayConfig,
    get_payment_gateway_config, get_active_payment_gateway,
    get_document_template,
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

    invoice_tpl = get_document_template("invoice")

    from ...models import get_financial_identity
    from ...services.documents import tectonic_health
    doc_health = tectonic_health()

    return render_template(
        "admin/financial.html",
        config=anzw_cfg,
        invoice=invoice_tpl,
        document_kinds=DOCUMENT_KINDS,
        ident=get_financial_identity(),
        doc_health=doc_health,
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


DOCUMENT_KINDS = {
    "invoice": ("Invoice", "Requests payment — sent to sponsors, or with a "
                           "registration that is not yet paid."),
    "receipt": ("Receipt", "Confirms a payment that has been received."),
    "adjustment": ("Adjustment note", "Records a refund or correction against "
                                      "an earlier payment."),
}


@admin_bp.route("/financial/invoice")
@requires_permission("financial.manage")
def financial_invoice():
    """Kept so existing links and bookmarks still land somewhere sensible."""
    return redirect(url_for("admin.financial_document", kind="invoice"))


@admin_bp.route("/financial/documents/<kind>", methods=["GET", "POST"])
@requires_permission("financial.manage")
def financial_document(kind):
    """Edit one document kind: the email cover and the PDF body. The issuer's
    tax and business details are shared across kinds and live on the Financial
    identity page."""
    if kind not in DOCUMENT_KINDS:
        flash("Unknown document type.", "error")
        return redirect(url_for("admin.financial"))

    tpl = get_document_template(kind)
    label, blurb = DOCUMENT_KINDS[kind]

    if request.method == "POST":
        tpl.subject = (request.form.get("subject") or "").strip()
        tpl.email_body = (request.form.get("email_body") or "").strip()
        tpl.from_name = (request.form.get("from_name") or "").strip()
        tpl.from_email = (request.form.get("from_email") or "").strip()
        tpl.footer_text = (request.form.get("footer_text") or "").strip()
        tpl.pdf_body = (request.form.get("pdf_body") or "").strip()
        db.session.commit()
        audit.record("financial.document_template_updated",
                     target_kind="document_template", target_id=str(tpl.id),
                     summary=f"{label} template updated by {current_user.email}")

        # The saved content changed the pregen's cache key — re-warm off-request
        # (in a thread) so the next preview serves the fresh cache without
        # stalling this save.
        from ...services.documents import warm_pregen_async
        warm_pregen_async(current_app._get_current_object(), kind)

        # from_email on a different domain to the SMTP sender fails SPF/DKIM
        # alignment on most providers — warn, don't block the save.
        if tpl.from_email:
            from email.utils import parseaddr
            _, sender_addr = parseaddr(current_app.config.get("MAIL_FROM", ""))
            sender_domain = sender_addr.split("@")[-1].lower() if "@" in sender_addr else ""
            from_domain = tpl.from_email.split("@")[-1].lower() if "@" in tpl.from_email else ""
            if sender_domain and from_domain and from_domain != sender_domain:
                flash(f"From email domain ({from_domain}) differs from the configured "
                      f"mail sender domain ({sender_domain}) — this mail may "
                      f"fail SPF/DKIM checks and land in spam.", "warning")

        flash(f"{label} template saved.", "success")
        return redirect(url_for("admin.financial_document", kind=kind))

    return render_template("admin/financial_document.html", tpl=tpl, kind=kind,
                           label=label, blurb=blurb, kinds=DOCUMENT_KINDS)


_ASSET_SLOTS = {"logo": "Letterhead logo", "signature": "Signature"}


@admin_bp.route("/financial/identity", methods=["GET", "POST"])
@requires_permission("financial.manage")
def financial_identity():
    """Who issues financial documents: legal entity, ABN, GST registration,
    address, payment details and signatory. One row, shared by every document
    kind — invoices, receipts and adjustment notes all render from it."""
    from ...models import get_financial_identity
    from ...services.documents import financial_assets_dir, warm_pregen_async
    from ...services.uploads import UploadError, save_fixed_png

    ident = get_financial_identity()

    if request.method == "POST":
        ident.legal_name = (request.form.get("legal_name") or "").strip()
        ident.abn = (request.form.get("abn") or "").strip()
        ident.gst_registered = request.form.get("gst_registered") == "1"
        ident.address = (request.form.get("address") or "").strip()
        ident.contact_email = (request.form.get("contact_email") or "").strip()
        ident.payment_instructions = (request.form.get("payment_instructions") or "").strip()
        ident.signatory_name = (request.form.get("signatory_name") or "").strip()
        ident.signatory_role = (request.form.get("signatory_role") or "").strip()

        # Both images are validated into a staging dir BEFORE either replaces a
        # live one. Assets live at fixed paths (logo.png / signature.png), so
        # writing them as we go would let a rejected signature leave a swapped
        # letterhead behind on a save the admin was told had failed — with none
        # of their text edits kept either.
        assets_dir = financial_assets_dir()
        assets_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=assets_dir) as staging:
            staged = {}
            for slot, label in _ASSET_SLOTS.items():
                fs = request.files.get(slot)
                if not (fs and fs.filename):
                    continue
                try:
                    name = save_fixed_png(
                        fs, dest_dir=Path(staging), name=slot,
                        max_bytes=current_app.config["MAX_FINANCIAL_ASSET_BYTES"])
                except UploadError as e:
                    db.session.rollback()
                    flash(f"{label}: {e}", "error")
                    return render_template("admin/financial_identity.html",
                                           ident=get_financial_identity())
                staged[slot] = name

            # Everything validated — publish. Same filesystem, so each move is
            # an atomic replace.
            for slot, name in staged.items():
                os.replace(Path(staging) / name, assets_dir / name)
                setattr(ident, f"{slot}_filename", name)

        db.session.commit()
        audit.record("financial.identity_updated",
                     target_kind="financial_identity", target_id=str(ident.id),
                     summary=f"Financial identity updated by {current_user.email}")

        # Identity feeds every kind's render, so all three pregens are stale.
        for kind in ("invoice", "receipt", "adjustment"):
            warm_pregen_async(current_app._get_current_object(), kind)

        flash("Financial identity saved.", "success")
        return redirect(url_for("admin.financial_identity"))

    return render_template("admin/financial_identity.html", ident=ident)


@admin_bp.route("/financial/identity/asset/<slot>")
@requires_permission("financial.manage")
def financial_identity_asset(slot):
    """Serve a financial asset to authorised admins only. These files live
    outside the public uploads tree precisely so they are never reachable
    without this permission check — a signature image is forgeable."""
    from ...models import get_financial_identity
    from ...services.documents import financial_assets_dir

    if slot not in _ASSET_SLOTS:
        return "", 404
    name = getattr(get_financial_identity(), f"{slot}_filename", "")
    path = financial_assets_dir() / name if name else None
    if not (path and path.is_file()):
        return "", 404
    return send_file(path, mimetype="image/png")


@admin_bp.route("/financial/identity/asset/<slot>/delete", methods=["POST"])
@requires_permission("financial.manage")
def financial_identity_asset_delete(slot):
    from ...models import get_financial_identity
    from ...services.documents import financial_assets_dir, warm_pregen_async

    if slot not in _ASSET_SLOTS:
        return redirect(url_for("admin.financial_identity"))
    ident = get_financial_identity()
    name = getattr(ident, f"{slot}_filename", "")
    if name:
        (financial_assets_dir() / name).unlink(missing_ok=True)
        setattr(ident, f"{slot}_filename", "")
        db.session.commit()
        for kind in ("invoice", "receipt", "adjustment"):
            warm_pregen_async(current_app._get_current_object(), kind)
    flash(f"{_ASSET_SLOTS[slot]} removed.", "success")
    return redirect(url_for("admin.financial_identity"))


@admin_bp.route("/financial/document/preview", methods=["POST"])
@requires_permission("financial.manage")
def financial_document_preview():
    """Download a PDF preview of the document editor's CURRENT (possibly unsaved)
    form. Serves the warm pregen when the submitted content matches the saved
    template and no data variable is overridden, otherwise recompiles fresh —
    the exact serve-vs-recompile rule lives in `documents.preview_pdf`. Never
    persists anything (preview is a pure caller of the one renderer)."""
    from io import BytesIO

    from ...models import DocumentTemplate
    from ...services.documents import (
        PregenBusy, RenderError, compile_backlog, preview_pdf,
    )

    kind = (request.form.get("kind") or "invoice").strip()
    if kind not in DOCUMENT_KINDS:
        flash("Unknown document type.", "error")
        return redirect(url_for("admin.financial"))

    # Queue-position report (plan §6). A synchronous request can't both wait on
    # a deep queue AND flash a position, so when compiles are already backed up
    # we tell the admin where they'd sit and return immediately — they retry in
    # a few seconds (by then the pregen is likely warm and serves instantly).
    # Only when the queue is idle (backlog 0) do we render inline as before.
    backlog = compile_backlog()
    if backlog > 0:
        flash(f"Your document is queued — position {backlog + 1} in line. "
              "Retry the download in a few seconds.", "info")
        return redirect(url_for("admin.financial_document", kind=kind))

    # An unsaved draft carrying the submitted editor fields, so edits that
    # aren't committed yet still drive the preview. Not added to the session —
    # it exists only to render and to compare content_hash against the saved row.
    draft = DocumentTemplate(
        kind=kind,
        pdf_body=(request.form.get("pdf_body") or "").strip(),
    )

    try:
        pdf = preview_pdf(kind, template=draft)
    except PregenBusy:
        flash("Preview is still compiling — retry in a few seconds.", "warning")
        return redirect(url_for("admin.financial_document", kind=kind))
    except RenderError as e:
        flash(f"Preview failed to compile: {e}"
              + (f"\n{e.log}" if e.log else ""), "error")
        return redirect(url_for("admin.financial_document", kind=kind))

    return send_file(BytesIO(pdf), mimetype="application/pdf",
                     as_attachment=True, download_name=f"preview-{kind}.pdf")


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
    from ...services.documents import RenderError
    from ...services.invoice import send_test_invoice

    to = (request.form.get("email") or "").strip() or current_user.email
    if "@" not in to:
        flash("Enter a valid email address for the test invoice.", "error")
        return redirect(url_for("admin.financial"))

    try:
        ok = send_test_invoice(to)
    except RenderError as e:
        flash(f"The test invoice PDF could not be generated: {e}"
              + (f"\n{e.log}" if e.log else ""), "error")
        return redirect(url_for("admin.financial"))
    audit.record("financial.test_invoice_sent",
                 target_kind="document_template", target_id="invoice",
                 summary=f"Test invoice sent to {to} by {current_user.email}")
    if ok:
        flash(f"Test invoice sent to {to}.", "success")
    else:
        flash("Failed to send test invoice — check the mail settings.", "error")
    return redirect(url_for("admin.financial"))


def _send_invoice_context(form=None):
    """Everything the Send Invoice template needs to render.

    The conference list, the sponsorship levels keyed by conference (so the
    level picker can follow the conference picker without a round trip), and
    the level prices the amount field is prefilled from.
    """
    from ...models import get_financial_identity
    from ...services.invoice import (
        default_conference, default_manual_invoice_body, invoiceable_conferences,
    )
    from ...services.jinja_filters import format_amount

    conferences = invoiceable_conferences()
    selected_id = None
    if form:
        try:
            selected_id = int(form.get("conference_id") or 0) or None
        except (TypeError, ValueError):
            selected_id = None
    if selected_id is None:
        default = default_conference()
        selected_id = default.id if default is not None else None

    levels = {
        str(c.id): [
            {"id": str(t.id), "name": t.name,
             "amount": format_amount(t.price) if t.price is not None else "",
             "item": f"{t.name} sponsorship"}
            for t in sorted(c.sponsor_tiers, key=lambda t: t.display_order)
        ]
        for c in conferences
    }
    return {
        "form": form if form is not None else {},
        "ident": get_financial_identity(),
        "default_body": default_manual_invoice_body(),
        "conferences": conferences,
        "selected_conference_id": selected_id,
        "levels": levels,
    }


def _resolve_send_invoice(form):
    """Turn the submitted form into the invoice's resolved fields, or errors.

    The sender chooses a conference and a sponsorship level; the line item, the
    amount, the billing period and the description all follow from that pair.
    The reference is never taken from the form — it is minted here (see
    `next_invoice_reference`), because it keys the ledger group, the pay link
    and the document's identity, and is not a judgement to hand to whoever
    happens to be raising an invoice.
    """
    from ...models import Conference
    from ...models.sponsor import SponsorTier
    from ...services.invoice import next_invoice_reference, sponsorship_line
    from ...services.jinja_filters import parse_cents

    errors = []
    to = (form.get("to") or "").strip()
    cc_raw = (form.get("cc") or "").replace(";", ",")
    cc = [a.strip() for a in cc_raw.split(",") if a.strip()]

    if "@" not in to:
        errors.append("Enter a valid recipient email.")
    errors += [f"Invalid CC address: {a}" for a in cc if "@" not in a]

    conference = None
    try:
        conference = Conference.query.get(int(form.get("conference_id") or 0))
    except (TypeError, ValueError):
        conference = None
    if conference is None:
        errors.append("Choose the conference this invoice is for.")

    tier = None
    tier_id = (form.get("tier_id") or "").strip()
    if tier_id and tier_id != "custom":
        try:
            tier = SponsorTier.query.get(int(tier_id))
        except (TypeError, ValueError):
            tier = None
        if tier is None or (conference and tier.conference_id != conference.id):
            errors.append("That sponsorship level does not belong to the "
                          "chosen conference.")
            tier = None

    line = sponsorship_line(conference, tier) if conference else {
        "description": "", "period": "", "item": "", "amount_cents": None}

    # A custom invoice supplies its own line; a sponsorship one may still
    # override the amount, since sponsorships get negotiated.
    item = line["item"]
    if tier is None:
        item = (form.get("item") or "").strip()
        if not item:
            errors.append("Describe the item being invoiced.")

    amount = line["amount_cents"]
    raw_amount = (form.get("amount") or "").strip()
    if raw_amount:
        try:
            amount = parse_cents(raw_amount)
        except ValueError:
            errors.append("Enter a valid amount, e.g. 500.00.")
            amount = None
    if amount is None:
        errors.append("This level has no price set — enter an amount, or set "
                      "the level's price on the conference.")
    elif amount <= 0:
        errors.append("Enter a valid amount, e.g. 500.00.")

    return {
        "errors": errors,
        "to": to,
        "cc": cc,
        "recipient_name": (form.get("recipient_name") or "").strip(),
        "description": line["description"],
        "period": line["period"],
        "item": item,
        "amount": amount or 0,
        "reference": next_invoice_reference(),
        "due_date": _display_date(form.get("due_date")),
        "recipient_abn": (form.get("recipient_abn") or "").strip(),
        "recipient_address": (form.get("recipient_address") or "").strip(),
        "include_gst": form.get("include_gst") == "1",
        "subject_override": (form.get("subject") or "").strip(),
        "body_override": (form.get("body") or "").strip(),
    }


def _display_date(raw):
    """`<input type="date">` submits ISO (2026-09-30); documents read as prose
    ("30 September 2026"). Anything unparseable passes through untouched."""
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%-d %B %Y")
    except ValueError:
        return text


@admin_bp.route("/financial/send-invoice", methods=["GET", "POST"])
@requires_permission("financial.manage")
def financial_send_invoice():
    """Send a templated invoice for an agreed amount to chosen recipients
    (e.g. sponsors), with the PDF attached and a durable pay link embedded."""
    from ...services.documents import RenderError
    from ...services.invoice import send_manual_invoice
    from ...services.jinja_filters import format_amount

    if request.method != "POST":
        return render_template("admin/financial_send_invoice.html",
                               **_send_invoice_context())

    f = _resolve_send_invoice(request.form)
    if f["errors"]:
        for e in f["errors"]:
            flash(e, "error")
        return render_template("admin/financial_send_invoice.html",
                               **_send_invoice_context(request.form))

    to, cc, reference, amount = f["to"], f["cc"], f["reference"], f["amount"]

    # §7 manual rule: a compile failure surfaces inline so the admin can fix
    # the template — nothing is recorded or sent (no degraded manual sends).
    try:
        ok = send_manual_invoice(
            to, cc=cc or None, recipient_name=f["recipient_name"],
            description=f["description"], item=f["item"], amount_cents=amount,
            reference=reference, period=f["period"],
            subject_override=f["subject_override"],
            body_override=f["body_override"],
            due_date=f["due_date"], recipient_abn=f["recipient_abn"],
            recipient_address=f["recipient_address"],
            include_gst=f["include_gst"],
        )
    except RenderError as e:
        flash(f"The invoice PDF could not be generated: {e}"
              + (f"\n{e.log}" if e.log else "")
              + "\nFix the document template, then try again.", "error")
        return render_template("admin/financial_send_invoice.html",
                               **_send_invoice_context(request.form))

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
                           **_send_invoice_context(request.form))


@admin_bp.route("/financial/send-invoice/preview", methods=["POST"])
@requires_permission("financial.manage")
def financial_send_invoice_preview():
    """Download a PDF preview of the invoice this form would send.

    Uses the same variable resolver as the send path, so the preview is the
    document the recipient will get — not an approximation. Records nothing and
    sends nothing; fields left blank show as their bold field names.
    """
    from io import BytesIO

    from ...services.documents import PregenBusy, RenderError, preview_document
    from ...services.invoice import manual_invoice_vars

    # Same resolver as the send path, so the conference/level pair drives the
    # preview's line item and amount exactly as it will drive the real send.
    # Errors are not fatal here: a half-filled form should still preview, with
    # the unfilled parts showing as bold field names.
    f = _resolve_send_invoice(request.form)
    reference = (f["reference"] if not f["errors"]
                 else f"INV-{datetime.utcnow().strftime('%Y%m%d')}-PREVIEW")

    vars_ = manual_invoice_vars(
        f["to"],
        recipient_name=f["recipient_name"],
        description=f["description"],
        item=f["item"],
        amount_cents=f["amount"],
        reference=reference,
        period=f["period"],
        due_date=f["due_date"],
        recipient_abn=f["recipient_abn"],
        recipient_address=f["recipient_address"],
        include_gst=f["include_gst"],
    )
    # Blank user fields stay a bold placeholder rather than an empty gap. But
    # the tax treatment is fully decided here (include_gst), so gst_applies must
    # carry through even when empty — "" means "GST off", not "unfilled". Drop
    # it and the identity-derived placeholder ("1" for a GST-registered society)
    # would win, so the preview would show a GST breakdown the send omits.
    # gst_registered rides along for the same reason: it decides which no-GST
    # statement the document prints, and "" is a real value there too.
    overrides = {k: v for k, v in vars_.items() if v not in ("", None)}
    overrides["gst_applies"] = vars_["gst_applies"]
    overrides["gst_registered"] = vars_["gst_registered"]
    # No amount chosen yet: keep the money fields as bold placeholders. A
    # formatted 0 is truthy and would otherwise print a real $0.00 — the one
    # thing a preview must never show, since it reads as a genuine total.
    if not f["amount"]:
        for money in ("amount", "gst_amount", "amount_ex_gst"):
            overrides.pop(money, None)

    try:
        pdf = preview_document("invoice", overrides)
    except PregenBusy:
        flash("Preview is still compiling — retry in a few seconds.", "warning")
        return redirect(url_for("admin.financial_send_invoice"))
    except RenderError as e:
        flash(f"Preview failed to compile: {e}" + (f"\n{e.log}" if e.log else ""),
              "error")
        return redirect(url_for("admin.financial_send_invoice"))

    return send_file(BytesIO(pdf), mimetype="application/pdf",
                     as_attachment=True,
                     download_name=f"preview-invoice-{reference}.pdf")



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


def _transaction_events(q: str):
    """The searchable financial-event ledger, newest first (latest 500). Shared
    by the transactions view and the bulk document download so both apply the
    exact same filter."""
    from ...models import PaymentEvent, Registration, User

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
    return (query.order_by(PaymentEvent.created_at.desc(),
                           PaymentEvent.id.desc())
            .limit(500).all())


def _issued_by_reference(refs):
    """Map merchant reference → its IssuedDocument rows (newest first) for the
    given references, so each transaction group can list its stored documents."""
    from ...models import IssuedDocument

    by_ref: dict[str, list] = {}
    if not refs:
        return by_ref
    docs = (IssuedDocument.query
            .filter(IssuedDocument.reference.in_(list(refs)))
            .order_by(IssuedDocument.issued_at.desc(), IssuedDocument.id.desc())
            .all())
    for d in docs:
        by_ref.setdefault(d.reference, []).append(d)
    return by_ref


@admin_bp.route("/financial/transactions")
@requires_permission("financial.manage")
def financial_transactions():
    """Financial event ledger, grouped per transaction and searchable."""
    q = (request.args.get("q") or "").strip()
    events = _transaction_events(q)

    grouped: dict[str, list] = {}
    for e in events:
        grouped.setdefault(e.group_key, []).append(e)

    by_ref = _issued_by_reference(grouped.keys())

    groups = []
    for key, evts in grouped.items():
        state = _transaction_state(evts)
        cancel_ref = ""
        ref = evts[0].merchant_reference or ""
        if ref.startswith("test_") and state in ("awaiting capture", "in progress"):
            if any(e.transaction_id and e.event_type != "checkout.created"
                   and not e.event_type.startswith("invoice.") for e in evts):
                cancel_ref = ref
        # Offer "Send pending document" when the group's most recent document.*
        # event is a document.pending (a §7 failed render awaiting retry).
        pending_ref = ""
        doc_evts = [e for e in evts if e.event_type.startswith("document.")]
        if doc_evts and max(doc_evts, key=lambda e: e.id).event_type == "document.pending":
            pending_ref = ref
        groups.append({"key": key, "events": evts, "state": state,
                       "cancel_ref": cancel_ref, "pending_ref": pending_ref,
                       "documents": by_ref.get(key, [])})

    return render_template("admin/financial_transactions.html",
                           groups=groups, q=q, event_count=len(events))


# Cap on a bulk document download so it stays inside the request/compile-queue
# budget (plan §12) — each document is a fresh regeneration.
_BULK_DOC_CAP = 20


@admin_bp.route("/financial/document/issued/<int:doc_id>/download", methods=["POST"])
@requires_permission("financial.manage")
def financial_download_issued_document(doc_id):
    """Regenerate a single stored document (plan §12) and stream it as a PDF.
    Rebuilds byte-identically from the snapshot; a compile failure flashes."""
    from io import BytesIO

    from ...models import IssuedDocument
    from ...services.documents import RenderError, regenerate_document
    from ...services.invoice import _safe_ref

    issued = db.session.get(IssuedDocument, doc_id)
    if not issued:
        flash("That document could not be found.", "error")
        return redirect(url_for("admin.financial_transactions"))

    try:
        pdf = regenerate_document(issued)
    except RenderError as e:
        flash(f"The document could not be regenerated: {e}"
              + (f"\n{e.log}" if e.log else ""), "error")
        return redirect(url_for("admin.financial_transactions"))

    return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=f"{issued.kind}-{_safe_ref(issued.reference)}.pdf")


@admin_bp.route("/financial/documents/download-zip", methods=["POST"])
@requires_permission("financial.manage")
def financial_download_documents_zip():
    """Regenerate every stored document matching the current search filter and
    stream them as a single in-memory zip (plan §12), capped at _BULK_DOC_CAP so
    the compile queue can't be swamped by one request. When more match, the cap
    is applied to the most-recent and a clear message is flashed."""
    import zipfile
    from io import BytesIO

    from ...services.documents import RenderError, regenerate_document
    from ...services.invoice import _safe_ref

    q = (request.args.get("q") or request.form.get("q") or "").strip()
    events = _transaction_events(q)
    refs = {e.group_key for e in events}
    by_ref = _issued_by_reference(refs)
    issued = [d for docs in by_ref.values() for d in docs]
    issued.sort(key=lambda d: (d.issued_at, d.id), reverse=True)

    if not issued:
        flash("No stored documents match the current filter.", "info")
        return redirect(url_for("admin.financial_transactions", q=q or None))

    total = len(issued)
    capped = issued[:_BULK_DOC_CAP]
    if total > _BULK_DOC_CAP:
        flash(f"{total} documents match — only the {_BULK_DOC_CAP} most recent "
              f"are included in the zip. Narrow the search to reach the rest.",
              "warning")

    buf = BytesIO()
    errors = 0
    written = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in capped:
            try:
                pdf = regenerate_document(d)
            except RenderError:
                errors += 1
                continue
            # id keeps names unique when a reference issued several documents.
            zf.writestr(f"{d.kind}-{_safe_ref(d.reference)}-{d.id}.pdf", pdf)
            written += 1

    if not written:
        flash("None of the matching documents could be regenerated.", "error")
        return redirect(url_for("admin.financial_transactions", q=q or None))
    if errors:
        flash(f"{errors} document(s) could not be regenerated and were skipped.",
              "warning")

    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="documents.zip")


def _transaction_state(evts) -> str:
    """Best-known lifecycle state of a transaction, derived from its events."""
    from datetime import datetime, timedelta

    suffixes = {e.event_type.rsplit(".", 1)[-1] for e in evts}
    types = {e.event_type for e in evts}
    if "refunded" in suffixes:
        return "refunded"
    if suffixes & {"chargebacked", "reversed", "chargeback_reversed"}:
        return "disputed"
    if suffixes & {"captured", "paid"}:
        return "paid"
    if suffixes & {"rejected", "rejected_capture"}:
        return "failed"
    if "cancelled" in suffixes:
        return "cancelled"
    if suffixes & {"pending_capture", "capture_requested"}:
        return "awaiting capture"
    if types == {"invoice.sent"}:
        return "invoice sent"
    if types == {"checkout.created"}:
        latest = max(e.created_at for e in evts if e.created_at)
        # hosted checkout sessions expire after roughly two hours
        if latest < datetime.utcnow() - timedelta(hours=3):
            return "abandoned"
        return "initiated"
    return "in progress"


@admin_bp.route("/financial/reconcile", methods=["POST"])
@requires_permission("financial.manage")
def financial_reconcile():
    """Fetch current payment state from Worldline for every unsettled
    registration and apply any outcomes whose webhooks were missed."""
    from ...services.reconcile import reconcile_payments

    summary = reconcile_payments()
    if summary["error"]:
        flash(f"Reconciliation not run: {summary['error']}", "error")
        return redirect(url_for("admin.financial_transactions"))

    audit.record("financial.reconciled",
                 target_kind="payment_gateway", target_id="anz_worldline",
                 summary=(f"Reconciliation by {current_user.email}: "
                          f"{summary['checked']} checked, "
                          f"{len(summary['changes'])} updated, "
                          f"{len(summary['errors'])} errors"))

    for ch in summary["changes"]:
        if ch.get("test_ref"):
            flash(f"Test payment {ch['test_ref']}: Worldline reports {ch['raw']} "
                  f"— recorded in the ledger (webhook may have been missed)",
                  "warning")
        else:
            flash(f"Registration {ch['reg_id']} ({ch['email']}): "
                  f"{ch['old']} → {ch['new']} (Worldline status {ch['raw']})",
                  "warning")
    for err in summary["errors"]:
        flash(f"Reconciliation error — {err}", "error")

    if summary.get("unchanged"):
        flash("Current Worldline state, no change needed — "
              + "; ".join(summary["unchanged"][:20])
              + ("; …" if len(summary["unchanged"]) > 20 else ""), "info")

    if summary["changes"]:
        flash(f"Reconciliation complete: {summary['checked']} checked, "
              f"{len(summary['changes'])} updated. Changes are recorded in "
              f"the ledger as reconcile.* events.", "success")
    else:
        flash(f"Reconciliation complete: {summary['checked']} checked, "
              f"no discrepancies found.", "success")
    return redirect(url_for("admin.financial_transactions"))


@admin_bp.route("/financial/test-payment/<ref>/cancel", methods=["POST"])
@requires_permission("financial.manage")
def financial_test_payment_cancel(ref):
    """Void the uncaptured authorization behind a test payment."""
    from flask import abort
    from ...models import PaymentEvent, record_payment_event
    from ...services.gateways.anz_worldline import ANZWorldlineGateway

    if not ref.startswith("test_"):
        abort(404)

    evt = (PaymentEvent.query
           .filter(PaymentEvent.merchant_reference == ref,
                   PaymentEvent.transaction_id != "",
                   PaymentEvent.event_type != "checkout.created",
                   db.not_(PaymentEvent.event_type.like("invoice.%")))
           .order_by(PaymentEvent.id.desc())
           .first())
    if not evt:
        flash(f"No payment found for {ref} to cancel.", "error")
        return redirect(url_for("admin.financial_transactions"))

    cfg = get_payment_gateway_config("anz_worldline")
    result = ANZWorldlineGateway(cfg).cancel_payment(evt.transaction_id)

    audit.record("financial.test_payment_cancelled",
                 target_kind="payment_gateway", target_id=ref,
                 summary=(f"Cancel authorization {evt.transaction_id} ({ref}) "
                          f"by {current_user.email}: "
                          f"{result.error or result.raw_status}"))

    if result.error:
        flash(f"Cancel failed for {ref}: {result.error}", "error")
    else:
        record_payment_event(
            transaction_id=evt.transaction_id,
            merchant_reference=ref,
            event_type=f"cancel.{(result.raw_status or 'requested').lower()}",
            amount=result.amount,
            note=f"authorization voided via API by {current_user.email}",
        )
        flash(f"Authorization for {ref} cancelled "
              f"(Worldline status {result.raw_status}). The hold on the card "
              f"will drop off within a few days.", "success")
    return redirect(url_for("admin.financial_transactions"))


@admin_bp.route("/financial/document/<ref>/send-pending", methods=["POST"])
@requires_permission("financial.manage")
def financial_send_pending_document(ref):
    """Retry a document whose automatic send failed (§7). Re-renders and
    re-sends the same document for a reference whose latest document.* event is
    still document.pending; on success records document.sent, otherwise surfaces
    the compile log so the admin can fix the template first."""
    from ...models import PaymentEvent, record_payment_event
    from ...services.documents import RenderError
    from ...services.invoice import resend_pending_document

    doc_evt = (PaymentEvent.query
               .filter(PaymentEvent.merchant_reference == ref,
                       PaymentEvent.event_type.like("document.%"))
               .order_by(PaymentEvent.id.desc())
               .first())
    if not doc_evt or doc_evt.event_type != "document.pending":
        flash(f"No pending document to send for {ref}.", "error")
        return redirect(url_for("admin.financial_transactions"))

    try:
        resend_pending_document(doc_evt)
    except RenderError as e:
        flash(f"The document still failed to generate: {e}"
              + (f"\n{e.log}" if e.log else "")
              + "\nFix the document template, then try again.", "error")
        return redirect(url_for("admin.financial_transactions"))

    record_payment_event(
        transaction_id=doc_evt.transaction_id,
        merchant_reference=ref,
        registration_id=doc_evt.registration_id,
        event_type="document.sent",
        amount=doc_evt.amount,
        note=f"pending document re-sent by {current_user.email}")
    audit.record("financial.document_resent",
                 target_kind="invoice", target_id=ref,
                 summary=f"Pending document for {ref} re-sent by {current_user.email}")
    flash(f"Document for {ref} generated and sent.", "success")
    return redirect(url_for("admin.financial_transactions"))
