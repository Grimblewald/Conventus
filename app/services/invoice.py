"""Invoice email rendering and sending."""
from __future__ import annotations

import logging
from datetime import datetime

from ..models import Registration, get_site_settings, get_invoice_template
from .jinja_filters import format_amount
from .mail import send_mail

log = logging.getLogger(__name__)


def _business_vars(tpl, amount_cents: int, gst: bool | None = None) -> dict:
    """Business/tax variables shared by every invoice email.

    GST-inclusive pricing: the GST component of an inclusive total is
    total ÷ 11 (10% GST). When GST is off, the breakdown collapses to
    zero-GST so templates render sensibly either way.
    """
    gst_on = tpl.gst_registered if gst is None else gst
    gst_cents = round(amount_cents / 11) if gst_on else 0
    return {
        "business_number": tpl.business_number or "",
        "payment_instructions": tpl.payment_instructions or "",
        "invoice_type": "Tax Invoice" if gst_on else "Invoice",
        "gst_amount": format_amount(gst_cents),
        "amount_ex_gst": format_amount(amount_cents - gst_cents),
        "due_date": "",
        "recipient_abn": "",
    }


def send_invoice_email(reg: Registration) -> bool:
    tpl = get_invoice_template()
    site = get_site_settings()
    user = reg.user
    conf = reg.conference

    vars_ = {
        "user_name": user.full_name or user.email,
        "user_email": user.email,
        "conference_title": conf.title,
        "conference_dates": conf.date_range,
        "tier_name": reg.tier_name,
        "amount": format_amount(reg.amount),
        "currency_code": site.currency_code,
        "currency_symbol": site.currency_symbol,
        "transaction_id": reg.transaction_id or "N/A",
        "payment_date": datetime.utcnow().strftime("%-d %B %Y"),
        "site_name": site.site_name,
        "registration_id": str(reg.id),
        **_business_vars(tpl, reg.amount),
    }

    kind = "refund" if reg.status == "refunded" else "payment"
    log.info("Sending %s invoice to %s for reg %d", kind, user.email, reg.id)

    return _send_rendered(vars_, to=user.email)


def send_test_invoice(to_email: str) -> bool:
    """Render the invoice template with sample data and email it to *to_email*.

    Lets admins proof the template without any payment taking place.
    """
    tpl = get_invoice_template()
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
        "payment_date": datetime.utcnow().strftime("%-d %B %Y"),
        "site_name": site.site_name,
        "registration_id": "0",
        **_business_vars(tpl, 100),
    }
    log.info("Sending test invoice to %s", to_email)
    return _send_rendered(vars_, to=to_email, subject_prefix="[TEST] ")


def send_manual_invoice(to: str, *, recipient_name: str, description: str,
                        item: str, amount_cents: int, reference: str,
                        period: str = "", cc: list[str] | None = None,
                        subject_override: str = "", body_override: str = "",
                        due_date: str = "", recipient_abn: str = "",
                        include_gst: bool | None = None) -> bool:
    """Send a templated invoice for an agreed amount to arbitrary recipients.

    Used for out-of-band billing (e.g. sponsors). Maps onto the template's
    registration-centric variables: *description* fills {conference_title},
    *item* fills {tier_name}, *reference* fills {transaction_id}.
    """
    tpl = get_invoice_template()
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
        "payment_date": datetime.utcnow().strftime("%-d %B %Y"),
        "site_name": site.site_name,
        "registration_id": "N/A",
        **_business_vars(tpl, amount_cents, gst=include_gst),
    }
    vars_["due_date"] = due_date
    vars_["recipient_abn"] = recipient_abn
    log.info("Sending manual invoice %s to %s (cc %s)", reference, to, cc or [])
    return _send_rendered(vars_, to=to, cc=cc, subject_override=subject_override,
                          body_override=body_override)


def default_manual_invoice_body(tpl) -> str:
    """Prefill for the Send Invoice form — worded as a request for payment
    (unlike the receipt-style automatic template), with the tax lines
    matching the template's GST setting."""
    gst_line = ("  Includes GST: {currency_symbol}{gst_amount}\n"
                if tpl.gst_registered else "")
    return (
        "{invoice_type} {transaction_id}\n"
        "{site_name}" + ("\nABN: {business_number}" if tpl.business_number else "") + "\n"
        "Issued: {payment_date}\n\n"
        "Bill to: {user_name}\n"
        "ABN: {recipient_abn}\n\n"
        "For: {conference_title}\n"
        "Item: {tier_name}\n"
        "Amount due: {currency_symbol}{amount} {currency_code}\n"
        + gst_line +
        "Due date: {due_date}\n\n"
        "Payment details:\n{payment_instructions}\n\n"
        "Please quote reference {transaction_id} with your payment.\n\n"
        "{site_name}"
    )


def _send_rendered(vars_: dict, to: str, subject_prefix: str = "",
                   cc: list[str] | None = None,
                   subject_override: str = "",
                   body_override: str = "") -> bool:
    tpl = get_invoice_template()
    site = get_site_settings()

    subject_tpl = subject_override or tpl.subject
    subject = subject_prefix + _render(subject_tpl, vars_)

    body_tpl = body_override or tpl.body_text
    body = _render(body_tpl, vars_)

    footer = _render(tpl.footer_text, vars_) if tpl.footer_text else ""
    if footer:
        body += f"\n\n{footer}"

    html = None
    if tpl.body_html and not body_override:
        from markupsafe import escape
        html = _render(tpl.body_html, vars_)
        if footer:
            html += f"\n<p>{escape(footer)}</p>"

    sender_name = _render(tpl.from_name, vars_) if tpl.from_name else None

    return send_mail(
        to=to,
        subject=subject,
        body=body,
        sender_name=sender_name or f"{site.site_name}",
        sender_email=(tpl.from_email or "").strip() or None,
        html=html,
        cc=cc,
    )


def _render(template: str, vars_: dict) -> str:
    for key, val in vars_.items():
        template = template.replace("{" + key + "}", str(val))
    return template
