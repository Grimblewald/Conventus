"""Invoice email rendering and sending."""
from __future__ import annotations

import logging
from datetime import datetime

from flask import current_app

from ..models import Registration, get_site_settings, get_invoice_template
from .mail import send_mail

log = logging.getLogger(__name__)


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
        "amount": reg.amount,
        "currency_code": site.currency_code,
        "currency_symbol": site.currency_symbol,
        "transaction_id": reg.transaction_id or "N/A",
        "payment_date": datetime.utcnow().strftime("%-d %B %Y"),
        "site_name": site.site_name,
        "registration_id": str(reg.id),
    }

    subject = _render(tpl.subject, vars_)
    body = _render(tpl.body_text, vars_)

    if tpl.footer_text:
        body += f"\n\n{_render(tpl.footer_text, vars_)}"

    sender_name = _render(tpl.from_name, vars_) if tpl.from_name else None

    kind = "refund" if reg.status == "refunded" else "payment"
    log.info("Sending %s invoice to %s for reg %d", kind, user.email, reg.id)

    return send_mail(
        to=user.email,
        subject=subject,
        body=body,
        sender_name=sender_name or f"{site.site_name}",
    )


def _render(template: str, vars_: dict) -> str:
    for key, val in vars_.items():
        template = template.replace("{" + key + "}", str(val))
    return template
