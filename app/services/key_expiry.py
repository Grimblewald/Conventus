"""Checks payment gateway API keys for imminent expiry and sends warnings."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..extensions import db
from ..models.content import PaymentGatewayConfig, get_site_settings
from ..models.user import User
from .mail import send_mail

log = logging.getLogger(__name__)

EXPIRY_WARNING_DAYS = [42, 14, 7, 6, 5, 4, 3, 2, 1]
API_KEY_VALIDITY_DAYS = 365


def check_key_expiry():
    configs = PaymentGatewayConfig.query.filter_by(is_enabled=True).all()
    if not configs:
        return

    site = get_site_settings()
    now = datetime.utcnow()

    for cfg in configs:
        if not cfg.api_key_set_at:
            continue

        expiry_date = cfg.api_key_set_at + timedelta(days=API_KEY_VALIDITY_DAYS)
        days_remaining = (expiry_date - now).days

        sent_warnings = cfg.expiry_warnings_sent or []

        for threshold in EXPIRY_WARNING_DAYS:
            if days_remaining <= threshold and threshold not in sent_warnings:
                _send_expiry_warning(cfg, days_remaining, site)
                sent_warnings.append(threshold)

        cfg.expiry_warnings_sent = sent_warnings
        db.session.commit()


def _send_expiry_warning(cfg, days_remaining, site):
    admins = User.query.filter(
        User.role_name == "admin",
        User.deleted_at.is_(None),
    ).all()

    for admin in admins:
        send_mail(
            to=admin.email,
            subject=f"[{site.site_name}] API key expires in {days_remaining} days",
            body=(
                f"The ANZ Worldline API key for {site.site_name} expires in "
                f"{days_remaining} day(s).\n\n"
                f"Provider: {cfg.provider}\n"
                f"Merchant ID: {cfg.merchant_id}\n"
                f"API Key ID: {cfg.api_key_id}\n\n"
                f"Please generate a new API key in the Merchant Portal and update "
                f"the settings in the Financial admin panel.\n\n"
                f"— {site.site_name}"
            ),
        )
    log.info("Sent API key expiry warning: %d days for %s", days_remaining, cfg.provider)
