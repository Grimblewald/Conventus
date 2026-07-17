"""Payment integration points.

Pluggable gateway architecture: register implementations via
``register_gateway()`` in ``app/services/gateways/``.

The active gateway is determined by the PaymentGatewayConfig DB model
(payment gateway configuration panel in the admin).
"""
from __future__ import annotations

import logging

from flask import current_app, url_for

from ..models.registration import Registration
from .mail import send_mail

log = logging.getLogger(__name__)


def _active_gateway():
    from ..models.content import get_active_payment_gateway
    config = get_active_payment_gateway()
    if not config or not config.is_enabled:
        return None
    from .gateways.anz_worldline import ANZWorldlineGateway
    return ANZWorldlineGateway(config)


def initiate_payment(registration: Registration) -> str | None:
    """Start a payment checkout for a registration.

    Returns a redirect URL the user should be sent to, or None if no
    gateway is configured (which means use the internal stub).
    """
    g = _active_gateway()
    if not g:
        return None
    result = g.create_checkout(
        registration,
        amount=registration.amount,
        currency=current_app.config.get("CURRENCY_CODE", "AUD"),
    )
    if result.error:
        log.warning("Payment error for reg %d: %s", registration.id, result.error)
        return None
    return result.redirect_url


def payment_url_for(registration: Registration) -> str:
    """Return the URL a member visits to pay for their registration."""
    redirect_url = initiate_payment(registration)
    if redirect_url:
        return redirect_url
    return url_for("member.pay_registration", reg_id=registration.id, _external=True)


def send_payment_email(registration: Registration, pay_url: str) -> bool:
    """Email the member a payment link for their registration."""
    conf = registration.conference
    body = (
        f"Thank you for registering for {conf.title} ({conf.date_range}).\n\n"
        f"Tier: {registration.tier_name}\n"
        f"Amount: {registration.amount}\n\n"
        f"To complete your registration, please visit:\n{pay_url}\n"
    )
    return send_mail(
        to=registration.user.email,
        subject=f"Payment for {conf.title}",
        body=body,
    )
