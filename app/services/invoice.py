"""Invoice email rendering and sending."""
from __future__ import annotations

import logging
from datetime import datetime

from ..models import Registration, get_site_settings, get_invoice_template
from .jinja_filters import format_amount
from .mail import send_mail

log = logging.getLogger(__name__)


def send_invoice_email(reg: Registration) -> bool:
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
    }

    kind = "refund" if reg.status == "refunded" else "payment"
    log.info("Sending %s invoice to %s for reg %d", kind, user.email, reg.id)

    return _send_rendered(vars_, to=user.email)


def send_test_invoice(to_email: str) -> bool:
    """Render the invoice template with sample data and email it to *to_email*.

    Lets admins proof the template without any payment taking place.
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
        "payment_date": datetime.utcnow().strftime("%-d %B %Y"),
        "site_name": site.site_name,
        "registration_id": "0",
    }
    log.info("Sending test invoice to %s", to_email)
    return _send_rendered(vars_, to=to_email, subject_prefix="[TEST] ")


def _send_rendered(vars_: dict, to: str, subject_prefix: str = "") -> bool:
    tpl = get_invoice_template()
    site = get_site_settings()

    subject = subject_prefix + _render(tpl.subject, vars_)
    body = _render(tpl.body_text, vars_)

    footer = _render(tpl.footer_text, vars_) if tpl.footer_text else ""
    if footer:
        body += f"\n\n{footer}"

    html = None
    if tpl.body_html:
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
    )


def _render(template: str, vars_: dict) -> str:
    for key, val in vars_.items():
        template = template.replace("{" + key + "}", str(val))
    return template
