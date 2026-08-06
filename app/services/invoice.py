"""Document send layer: renders the PDF for a document kind and emails the
plaintext cover with it attached.

This module is the `send_document` layer of the plan (§2): the three public
senders below are thin wrappers that resolve real variables, render the
matching PDF kind through the ONE renderer (`documents.render_document`) and
email the DocumentTemplate cover with the PDF attached. It never re-implements
rendering. Automatic (webhook-triggered) sends carry the §7 failure handling;
manual/test sends surface a compile error to the admin instead.
"""
from __future__ import annotations

import logging
import re
import types
from datetime import datetime

from flask import url_for

from ..models import (
    PaymentEvent, Registration, record_payment_event,
    get_site_settings, get_document_template, get_financial_identity,
)
from .documents import RenderError, render_document
from .jinja_filters import format_amount
from .mail import send_mail

log = logging.getLogger(__name__)


def _business_vars(amount_cents: int, gst: bool | None = None) -> dict:
    """Issuer and tax variables shared by every document, from the single
    FinancialIdentity row.

    GST-inclusive pricing: the GST component of an inclusive total is
    total ÷ 11 (10% GST). `gst` overrides the identity's registration for one
    send (the Send Invoice form's per-recipient toggle). `gst_applies` is the
    control flag the renderer keys on — it travels in the variables so a
    stored document regenerates with the tax treatment it was issued under,
    not today's setting.

    `gst_registered` is a SEPARATE fact and must stay separate: "no GST on this
    sale" and "this issuer is not registered for GST" are different statements,
    and a registered issuer printing the latter on a tax document would be
    asserting something false. Both travel with the document for the same
    snapshot reason.
    """
    ident = get_financial_identity()
    site = get_site_settings()
    gst_on = ident.gst_registered if gst is None else gst
    gst_cents = round(amount_cents / 11) if gst_on else 0
    return {
        "business_legal_name": ident.legal_name or site.site_name,
        "business_number": ident.abn or "",
        "business_address": ident.address or "",
        "business_contact_email": ident.contact_email or "",
        "payment_instructions": ident.payment_instructions or "",
        "signatory_name": ident.signatory_name or "",
        "signatory_role": ident.signatory_role or "",
        "gst_applies": "1" if gst_on else "",
        "gst_registered": "1" if ident.gst_registered else "",
        "invoice_type": "Tax Invoice" if gst_on else "Invoice",
        "gst_amount": format_amount(gst_cents),
        "amount_ex_gst": format_amount(amount_cents - gst_cents),
        "due_date": "",
        "recipient_abn": "",
        "recipient_address": "",
    }


# ---------------------------------------------------------------------------
# Automatic sends (webhook / reconcile) — receipt on payment, adjustment on
# refund, with the §7 failure handling around the render.
# ---------------------------------------------------------------------------

def send_invoice_email(reg: Registration) -> bool:
    """Auto document for a settled registration: a receipt when paid, an
    adjustment note when refunded (plan §2 mapping). Renders the matching PDF
    and emails that kind's cover with it attached; on a compile failure the
    payment is already final, so §7 keeps the webhook safe."""
    kind = "adjustment" if reg.status == "refunded" else "receipt"
    vars_ = _registration_vars(reg, kind)
    log.info("Sending %s to %s for reg %d", kind, vars_["user_email"], reg.id)
    return _send_auto_document(
        kind, vars_, to=vars_["user_email"], reg=reg,
        merchant_reference=_reg_merchant_reference(reg),
        transaction_id=reg.transaction_id or "", amount_cents=reg.amount)


def send_manual_invoice_receipt(reference: str, *, amount_cents: int | None = None,
                                transaction_id: str = "") -> bool:
    """Send the receipt for a manual invoice paid through its durable link (§8),
    triggered by the capture webhook. The recipient is recovered from the
    invoice's ledger note. Auto-send, so §7 failure handling applies. Returns
    False (a no-op) when the recipient can't be recovered."""
    recipient = _invoice_recipient(reference)
    if not recipient:
        log.warning("Cannot send receipt for %s — recipient not recoverable", reference)
        return False
    vars_ = _manual_receipt_vars(reference, recipient, amount_cents or 0)
    log.info("Sending receipt to %s for invoice %s", recipient, reference)
    return _send_auto_document(
        "receipt", vars_, to=recipient, reg=None, merchant_reference=reference,
        transaction_id=transaction_id, amount_cents=amount_cents)


def resend_pending_document(evt: PaymentEvent) -> bool:
    """Re-render and re-send a document whose earlier automatic send failed
    (§7 retry). Raises RenderError if it still won't compile so the admin sees
    the log; records nothing itself — the caller records `document.sent`. The
    kind and recipient are reconstructed from the pending event."""
    if evt.registration_id:
        reg = Registration.query.get(evt.registration_id)
        if not reg:
            raise RenderError("registration no longer exists")
        kind = "adjustment" if reg.status == "refunded" else "receipt"
        vars_ = _registration_vars(reg, kind)
        to = vars_["user_email"]
    else:
        ref = evt.merchant_reference
        to = _invoice_recipient(ref)
        if not to:
            raise RenderError(f"recipient for {ref} not recoverable")
        kind = "receipt"
        vars_ = _manual_receipt_vars(ref, to, evt.amount or 0)
    attachment = _render_attachment(kind, vars_, vars_["transaction_id"])
    ok = _send_rendered(kind, vars_, to=to, attachment=attachment)
    if ok:
        _record_issued(kind, vars_, reference=evt.merchant_reference or "",
                       recipient=to, amount_cents=evt.amount)
    return ok


# ---------------------------------------------------------------------------
# Manual + test sends (admin-triggered) — a compile error surfaces inline.
# ---------------------------------------------------------------------------

def send_test_invoice(to_email: str) -> bool:
    """Render the invoice template with sample data and email it to *to_email*.

    Lets admins proof the template without any payment taking place. A compile
    failure raises RenderError for the route to surface inline (§7 manual rule).
    """
    site = get_site_settings()
    vars_ = {
        "user_name": "Test Attendee",
        "user_email": to_email,
        "conference_title": "Sample Conference",
        "conference_dates": "1–3 September 2026",
        "tier_name": "Standard",
        "amount": format_amount(100),
        "currency_code": site.currency_code,
        "currency_symbol": site.currency_symbol,
        "transaction_id": "TEST-000000",
        "payment_reference": "TEST-000000",
        "payment_date": datetime.utcnow().strftime("%-d %B %Y"),
        "site_name": site.site_name,
        "registration_id": "0",
        **_business_vars(100),
    }
    # The invoice cover asks the payer to "Pay online: {payment_link}". A proof
    # send must resolve it like a real one does, or the admin receives the raw
    # placeholder text. The reference is fictional, so the link lands on the
    # "payment link not available" page — which is itself worth proofing.
    vars_["payment_link"] = url_for("public.pay_invoice",
                                    reference="TEST-000000", _external=True)
    _finalise_issuer_text(vars_)
    log.info("Sending test invoice to %s", to_email)
    attachment = _render_attachment("invoice", vars_, "TEST-000000")
    return _send_rendered("invoice", vars_, to=to_email, subject_prefix="[TEST] ",
                          attachment=attachment)


# ---------------------------------------------------------------------------
# Raising an invoice from the catalogue: which conference, which sponsorship
# level. The sender picks two things; the line item, the amount, the billing
# period and the reference all follow from them.
# ---------------------------------------------------------------------------

def conference_proximity(conference, today=None) -> int:
    """Days between *today* and the conference, in either direction.

    Zero while it is running. Direction is deliberately discarded: an invoice
    raised the week after a meeting is exactly as likely as one raised the week
    before, so what makes a conference relevant is how near it is to now, not
    whether it has happened yet.
    """
    today = today or datetime.utcnow().date()
    if conference.start_date <= today <= conference.end_date:
        return 0
    return min(abs((conference.start_date - today).days),
               abs((conference.end_date - today).days))


def invoiceable_conferences():
    """Every conference, nearest to now first — past and future interleaved.

    Ordering by absolute distance keeps whatever is actually current at the top
    of the picker: the meeting in progress, then the one just finished or about
    to start, with long-ago and far-off events trailing. Ties break toward the
    later date, so a future meeting outranks an equidistant past one.

    Soft-deleted conferences are excluded; drafts are not, since sponsorship is
    routinely agreed before the conference page goes live.
    """
    from ..models import Conference

    today = datetime.utcnow().date()
    live = Conference.query.filter(Conference.deleted_at.is_(None)).all()
    return sorted(live,
                  key=lambda c: (conference_proximity(c, today), -c.start_date.toordinal()))


def default_conference():
    """The conference an invoice most likely concerns: simply the nearest one
    to now (see `invoiceable_conferences`)."""
    conferences = invoiceable_conferences()
    return conferences[0] if conferences else None


def sponsorship_line(conference, tier) -> dict:
    """The invoice line for a sponsorship level: what it is, what it costs, and
    the period it covers — all derived, none retyped.

    `tier` may be None for a custom (non-sponsorship) invoice, in which case
    only the conference-derived fields come back and the caller supplies the
    rest.
    """
    line = {
        "description": f"Sponsorship — {conference.title}" if tier is not None
                       else conference.title,
        "period": conference.date_range,
        "item": "",
        "amount_cents": None,
    }
    if tier is not None:
        line["item"] = f"{tier.name} sponsorship"
        line["amount_cents"] = tier.price
    return line


def next_invoice_reference() -> str:
    """A fresh, unused invoice reference.

    Never solicited from the sender: the reference keys the ledger group, the
    durable pay link and the document's identity, it must be unique and ≤30
    characters, and none of that is a judgement an admin should be asked to
    make while raising an invoice. Retries on the (vanishingly unlikely)
    collision rather than handing a duplicate back to the caller.
    """
    import secrets

    stamp = datetime.utcnow().strftime("%Y%m%d")
    for _ in range(20):
        ref = f"INV-{stamp}-{secrets.token_hex(2).upper()}"
        if not PaymentEvent.query.filter_by(merchant_reference=ref).count():
            return ref
    # 20 collisions on 16 bits within one day: widen rather than fail.
    return f"INV-{stamp}-{secrets.token_hex(4).upper()}"[:30]


def sanitized_reference(reference: str) -> str:
    """The reference in a form that survives a bank-transfer reference field.

    A BECS lodgement reference is 18 characters from a restricted set, and
    banks differ on whether they accept punctuation — so a payer quoting
    `INV-20260806-28D4` on a transfer may have the hyphens stripped or the
    field rejected outright. Stripping them here gives the payer something to
    copy that is already bank-safe, well inside 18 characters.

    Lossless, deliberately: the letters stay, so two references can never
    sanitize to the same string. Dropping them for a digits-only form would
    map INV-…-28D4 and INV-…-284D onto one number, and nothing downstream
    could tell the two invoices apart.
    """
    return re.sub(r"[^A-Za-z0-9]", "", str(reference or "")).upper()


def _finalise_issuer_text(vars_: dict) -> dict:
    """Publish the bank-safe reference, and resolve variables *inside* the
    configured payment instructions.

    payment_instructions is issuer-configured free text that reaches the email
    body and the PDF as a leaf value, and `_render` is a single pass — so a
    placeholder written inside it would otherwise print literally. Expanding it
    here, against everything else already resolved, is what lets an admin write
    "REF: {sanitized_invoice_ref}" in Financial identity and have it come out
    as the reference. Self-reference is excluded so the block cannot embed a
    copy of itself.
    """
    # The reference the PAYER quotes, which is not always the gateway's
    # transaction id: a registration's is REG-000123, derived from the id and
    # present from the moment they register, while transaction_id stays null
    # until a payment settles. Falls back to transaction_id for documents that
    # predate the distinction.
    vars_["sanitized_invoice_ref"] = sanitized_reference(
        vars_.get("payment_reference") or vars_.get("transaction_id", ""))
    text = vars_.get("payment_instructions") or ""
    if text:
        vars_["payment_instructions"] = _render(
            text, {k: v for k, v in vars_.items() if k != "payment_instructions"})
    return vars_


def manual_invoice_vars(to: str, *, recipient_name: str, description: str,
                        item: str, amount_cents: int, reference: str,
                        period: str = "", due_date: str = "",
                        recipient_abn: str = "", recipient_address: str = "",
                        include_gst: bool | None = None) -> dict:
    """Resolve the variables for a manually raised invoice.

    Shared by the send path and the form's preview so what an admin previews is
    exactly what the recipient receives — a preview built from its own copy of
    this mapping would drift from reality the first time either changed.

    Maps onto the registration-centric vocabulary: *description* fills
    {conference_title}, *item* fills {tier_name}, *reference* fills
    {transaction_id}.
    """
    site = get_site_settings()
    vars_ = {
        "user_name": recipient_name or to,
        "user_email": to,
        "conference_title": description,
        "conference_dates": period,
        "tier_name": item,
        "amount": format_amount(amount_cents),
        "currency_code": site.currency_code,
        "currency_symbol": site.currency_symbol,
        "transaction_id": reference,
        "payment_reference": reference,
        "payment_date": datetime.utcnow().strftime("%-d %B %Y"),
        "site_name": site.site_name,
        "registration_id": "N/A",
        **_business_vars(amount_cents, gst=include_gst),
    }
    vars_["due_date"] = due_date
    vars_["recipient_abn"] = recipient_abn
    vars_["recipient_address"] = recipient_address
    # Durable pay link — points at our /pay/invoice/<ref> route, not the
    # ephemeral Worldline URL, which expires (§8).
    vars_["payment_link"] = url_for("public.pay_invoice", reference=reference,
                                    _external=True)
    return _finalise_issuer_text(vars_)


def send_manual_invoice(to: str, *, recipient_name: str, description: str,
                        item: str, amount_cents: int, reference: str,
                        period: str = "", cc: list[str] | None = None,
                        subject_override: str = "", body_override: str = "",
                        due_date: str = "", recipient_abn: str = "",
                        recipient_address: str = "",
                        include_gst: bool | None = None) -> bool:
    """Send a templated invoice for an agreed amount to arbitrary recipients.

    Used for out-of-band billing (e.g. sponsors). Maps onto the template's
    registration-centric variables: *description* fills {conference_title},
    *item* fills {tier_name}, *reference* fills {transaction_id}. Embeds the
    durable pay link (§8) as {payment_link}, resolved here (the renderer stays
    agnostic). A compile failure raises RenderError for the route to surface
    inline (§7 manual rule) — nothing is sent.
    """
    vars_ = manual_invoice_vars(
        to, recipient_name=recipient_name, description=description, item=item,
        amount_cents=amount_cents, reference=reference, period=period,
        due_date=due_date, recipient_abn=recipient_abn,
        recipient_address=recipient_address, include_gst=include_gst)
    log.info("Sending manual invoice %s to %s (cc %s)", reference, to, cc or [])
    attachment = _render_attachment("invoice", vars_, reference)
    ok = _send_rendered("invoice", vars_, to=to, cc=cc,
                        subject_override=subject_override,
                        body_override=body_override, attachment=attachment)
    if ok:
        _record_issued("invoice", vars_, reference=reference, recipient=to,
                       amount_cents=amount_cents)
    return ok


def default_manual_invoice_body() -> str:
    """Prefill for the Send Invoice form — worded as a request for payment
    (unlike the receipt-style automatic template), with the tax lines matching
    the financial identity's GST registration."""
    ident = get_financial_identity()
    gst_line = ("  Includes GST: {currency_symbol}{gst_amount}\n"
                if ident.gst_registered else "")
    return (
        "{invoice_type} {transaction_id}\n"
        "{business_legal_name}" + ("\nABN: {business_number}" if ident.abn else "") + "\n"
        "Issued: {payment_date}\n\n"
        "Bill to: {user_name}\n"
        "ABN: {recipient_abn}\n\n"
        "For: {conference_title}\n"
        "Item: {tier_name}\n"
        "Amount due: {currency_symbol}{amount} {currency_code}\n"
        + gst_line +
        "Due date: {due_date}\n\n"
        "Pay online:\n{payment_link}\n\n"
        "Payment details:\n{payment_instructions}\n\n"
        "Please quote reference {transaction_id} with your payment.\n\n"
        "{business_legal_name}"
    )


# ---------------------------------------------------------------------------
# Variable resolution — shared by the initial send and the §7 retry.
# ---------------------------------------------------------------------------

def _registration_vars(reg: Registration, kind: str) -> dict:
    """Real document variables for a settled registration."""
    site = get_site_settings()
    user = reg.user
    conf = reg.conference
    return _finalise_issuer_text({
        "user_name": user.full_name or user.email,
        "user_email": user.email,
        "conference_title": conf.title,
        "conference_dates": conf.date_range,
        "tier_name": reg.tier_name,
        "amount": format_amount(reg.amount),
        "currency_code": site.currency_code,
        "currency_symbol": site.currency_symbol,
        "transaction_id": reg.transaction_id or "N/A",
        "payment_reference": reg.reference,
        "payment_date": datetime.utcnow().strftime("%-d %B %Y"),
        "site_name": site.site_name,
        "registration_id": str(reg.id),
        **_business_vars(reg.amount),
    })


def _manual_receipt_vars(reference: str, recipient: str,
                         amount_cents: int) -> dict:
    """Real document variables for a manual invoice's receipt (paid via the
    durable link). The invoice's own reference fills {transaction_id}."""
    site = get_site_settings()
    return _finalise_issuer_text({
        "user_name": recipient,
        "user_email": recipient,
        "conference_title": "",
        "conference_dates": "",
        "tier_name": "",
        "amount": format_amount(amount_cents),
        "currency_code": site.currency_code,
        "currency_symbol": site.currency_symbol,
        "transaction_id": reference,
        "payment_reference": reference,
        "payment_date": datetime.utcnow().strftime("%-d %B %Y"),
        "site_name": site.site_name,
        "registration_id": "N/A",
        **_business_vars(amount_cents),
    })


def _reg_merchant_reference(reg: Registration) -> str:
    """The merchant reference this registration's payment is grouped under in
    the ledger, so a document.* event lands in the same transaction group as
    the payment. Falls back to the legacy reg_<id> form."""
    e = (PaymentEvent.query
         .filter(PaymentEvent.registration_id == reg.id,
                 PaymentEvent.merchant_reference != "")
         .order_by(PaymentEvent.id.desc())
         .first())
    return e.merchant_reference if e else f"reg_{reg.id}"


def _invoice_recipient(reference: str) -> str:
    """Recover a manual invoice's recipient email from its `invoice.sent` ledger
    note (recorded as `to <email> ...` — plan §8, no schema change). '' if not
    found."""
    evt = (PaymentEvent.query
           .filter_by(merchant_reference=reference, event_type="invoice.sent")
           .order_by(PaymentEvent.id.desc())
           .first())
    if not evt:
        return ""
    m = re.search(r"to\s+(\S+@\S+)", evt.note or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Render + email primitives (raise on compile failure).
# ---------------------------------------------------------------------------

def _render_attachment(kind: str, vars_: dict, reference: str) -> tuple:
    """Render the `kind` PDF through the one renderer and wrap it as a send_mail
    attachment tuple. Raises RenderError on compile failure (callers decide how
    to handle it — inline for manual sends, degraded for auto sends)."""
    pdf = render_document(kind, vars_)
    filename = f"{kind}-{_safe_ref(reference)}.pdf"
    return (filename, pdf, "application/pdf")


def _safe_ref(reference: str) -> str:
    ref = re.sub(r"[^A-Za-z0-9._-]", "", str(reference or ""))
    return (ref or "document")[:60]


def _record_issued(kind: str, vars_: dict, *, reference: str, recipient: str,
                   amount_cents: int | None) -> None:
    """Append an IssuedDocument snapshot for a real send that just attached a
    PDF (plan §12, ATO 5-year retention). Captures the EXACT resolved variables
    and the render-affecting template fields used, so `regenerate_document` can
    rebuild the PDF byte-identically later even after the live template changes.
    Called next to `_render_attachment`'s successful send paths only — never for
    test invoices or previews. Best-effort: a snapshot failure must never break
    a send that already went out."""
    import json

    from ..models import db, IssuedDocument
    try:
        tpl = get_document_template(kind)
        # Only pdf_body is template-side now: every issuer/tax value already
        # travels in vars_ (resolved from the financial identity at send
        # time), so the variable snapshot alone pins the tax treatment.
        template_json = json.dumps({"pdf_body": tpl.pdf_body or ""})
        db.session.add(IssuedDocument(
            kind=kind,
            reference=reference or "",
            recipient=recipient or "",
            amount=amount_cents,
            vars_json=json.dumps({k: str(v) for k, v in vars_.items()}),
            template_json=template_json,
            content_hash=tpl.content_hash,
        ))
        db.session.commit()
    except Exception:
        log.exception("Failed to record issued document (%s / %s)", kind, reference)
        try:
            db.session.rollback()
        except Exception:
            pass


def _send_rendered(kind: str, vars_: dict, to: str, subject_prefix: str = "",
                   cc: list[str] | None = None, subject_override: str = "",
                   body_override: str = "", body_suffix: str = "",
                   attachment: tuple | None = None) -> bool:
    """Email the plaintext cover for `kind` (its DocumentTemplate email fields),
    optionally with a rendered PDF attached and/or an appended paragraph."""
    tpl = get_document_template(kind)
    site = get_site_settings()

    subject = subject_prefix + _render(subject_override or tpl.subject, vars_)
    body = _render(body_override or tpl.email_body, vars_)

    footer = _render(tpl.footer_text, vars_) if tpl.footer_text else ""
    if footer:
        body += f"\n\n{footer}"
    if body_suffix:
        body += body_suffix

    sender_name = _render(tpl.from_name, vars_) if tpl.from_name else None

    return send_mail(
        to=to,
        subject=subject,
        body=body,
        sender_name=sender_name or f"{site.site_name}",
        sender_email=(tpl.from_email or "").strip() or None,
        cc=cc,
        attachments=[attachment] if attachment else None,
    )


# ---------------------------------------------------------------------------
# §7 automatic-send failure handling.
# ---------------------------------------------------------------------------

def _send_auto_document(kind: str, vars_: dict, *, to: str,
                        reg: Registration | None, merchant_reference: str,
                        transaction_id: str = "",
                        amount_cents: int | None = None) -> bool:
    """Auto (webhook/reconcile) document send with the §7 failure handling.

    Renders the `kind` PDF, emails the cover with it attached, and records a
    `document.sent` ledger event. On RenderError the payment/refund is already
    final, so instead we email a plaintext confirmation, alert admins with the
    compile log, and record `document.pending` for later retry. Always returns
    True — a webhook must never fail because a document didn't render."""
    reference = vars_.get("transaction_id") or merchant_reference
    try:
        attachment = _render_attachment(kind, vars_, reference)
    except RenderError as err:
        _document_render_failed(
            kind, vars_, to=to, reg=reg, merchant_reference=merchant_reference,
            transaction_id=transaction_id, amount_cents=amount_cents, err=err)
        return True

    ok = _send_rendered(kind, vars_, to=to, attachment=attachment)
    if ok:
        _record_issued(kind, vars_, reference=merchant_reference, recipient=to,
                       amount_cents=amount_cents)
    record_payment_event(
        transaction_id=transaction_id,
        merchant_reference=merchant_reference,
        registration_id=reg.id if reg else None,
        event_type="document.sent",
        amount=amount_cents,
        note=f"{kind} document emailed to {to}")
    return ok


_DOC_NAMES = {"receipt": "receipt", "adjustment": "adjustment note",
              "invoice": "invoice"}


def _document_render_failed(kind: str, vars_: dict, *, to: str,
                            reg: Registration | None, merchant_reference: str,
                            transaction_id: str, amount_cents: int | None,
                            err: RenderError) -> None:
    """§7: a compile failed on an automatic send. The payment/refund already
    succeeded, so confirm it in plaintext, alert admins with the compile log,
    and record `document.pending` so the failure is visible and retryable."""
    doc_name = _DOC_NAMES.get(kind, "document")
    action = "refund" if kind == "adjustment" else "payment"
    log.exception("Rendering the %s for %s failed: %s",
                  doc_name, merchant_reference, err)

    # 1. Degraded email — the payment is confirmed; the formal document follows.
    notice = (
        f"\n\nYour {action} has been processed successfully. Unfortunately an "
        f"internal error prevented us from generating your formal {doc_name} at "
        f"this time. The error has been logged and our administrators have been "
        f"notified; we will send the {doc_name} to you as soon as the issue is "
        f"resolved.")
    _send_rendered(kind, vars_, to=to, body_suffix=notice)

    # 2. Alert admins through the payment-attention channel, with the log.
    reason = (
        f"Generating the {doc_name} for a completed {action} failed. The "
        f"{action} itself succeeded and is recorded — only the document is "
        f"outstanding. Fix the document template, then use “Send pending "
        f"document” for reference {merchant_reference} on the transactions "
        f"ledger.\n\nCompile log:\n" + (err.log or str(err)))
    if reg is not None:
        from ..blueprints.public import _notify_payment_attention
        _notify_payment_attention(
            reg, types.SimpleNamespace(event_type="document.render_failed",
                                       amount=reg.amount,
                                       transaction_id=reg.transaction_id),
            reason=reason)
    else:
        _alert_admins_document_failure(merchant_reference, amount_cents, reason)

    # 3. Ledger event so the pending document shows in the transactions view.
    record_payment_event(
        transaction_id=transaction_id,
        merchant_reference=merchant_reference,
        registration_id=reg.id if reg else None,
        event_type="document.pending",
        amount=amount_cents,
        note=f"{kind}: {_error_summary(err)}")


def _alert_admins_document_failure(reference: str, amount_cents: int | None,
                                   reason: str) -> None:
    """Payment-attention alert for a failure with no registration (a manual
    invoice paid via its link). Mirrors the webhook's admin channel."""
    from ..models.user import User
    from ..security import audit

    site = get_site_settings()
    amount = format_amount(amount_cents) if amount_cents is not None else "?"
    audit.record("financial.payment_attention",
                 target_kind="invoice", target_id=reference,
                 summary=f"Document render failed for invoice {reference} (${amount})")
    admins = User.query.filter(User.role_name == "admin",
                               User.deleted_at.is_(None)).all()
    for admin in admins:
        send_mail(
            to=admin.email,
            subject=f"[{site.site_name}] Document needs attention: {reference}",
            body=(f"A document could not be generated after a payment.\n\n"
                  f"Invoice reference: {reference}\n"
                  f"Amount: ${amount}\n\n"
                  f"{reason}"))


def _error_summary(err: RenderError) -> str:
    """A short, single-line summary of a compile failure for the ledger note."""
    tail = (err.log or str(err)).strip().splitlines()
    return (tail[-1] if tail else str(err))[:200]


def _render(template: str, vars_: dict) -> str:
    for key, val in vars_.items():
        template = template.replace("{" + key + "}", str(val))
    return template
