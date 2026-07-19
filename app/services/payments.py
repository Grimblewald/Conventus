"""Payment integration points.

Pluggable gateway architecture: register implementations via
``register_gateway()`` in ``app/services/gateways/``.

The active gateway is determined by the PaymentGatewayConfig DB model
(payment gateway configuration panel in the admin).
"""
from __future__ import annotations

import logging

from flask import url_for

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


def gateway_available() -> bool:
    """True when an enabled payment gateway is configured."""
    return _active_gateway() is not None


def sandbox_mode() -> bool:
    """True when the enabled gateway is in sandbox (test) mode."""
    from ..models.content import get_active_payment_gateway
    config = get_active_payment_gateway()
    return bool(config and config.is_test_mode)


def payments_open_to_members() -> bool:
    """True when general members may pay online.

    Requires all three: gateway enabled, live (not sandbox) mode, and the
    site-level payment portal switch. While any of these is off, members
    see the portal as unavailable; admins and financial.manage holders can
    still run test payments through an enabled gateway.
    """
    from ..models.content import get_active_payment_gateway, get_site_settings
    config = get_active_payment_gateway()
    if not config or config.is_test_mode:
        return False
    return bool(get_site_settings().payment_portal_enabled)


def initiate_payment(registration: Registration) -> str | None:
    """Start a payment checkout for a registration.

    Returns a redirect URL the user should be sent to, or None if no
    gateway is configured (which means use the internal stub).
    """
    g = _active_gateway()
    if not g:
        return None
    from ..models.content import get_site_settings
    result = g.create_checkout(
        registration,
        amount=registration.amount,
        currency=(get_site_settings().currency_code or "AUD").upper(),
    )
    if result.error:
        log.warning("Payment error for reg %d: %s", registration.id, result.error)
        return None
    from ..models import record_payment_event
    record_payment_event(
        transaction_id=result.payment_id,
        merchant_reference=f"reg_{registration.id}",
        registration_id=registration.id,
        event_type="checkout.created",
        amount=registration.amount,
        note="hosted checkout session created",
    )
    return result.redirect_url


def payment_url_for(registration: Registration) -> str:
    """Return the URL a member visits to pay — always our confirmation page."""
    return url_for("member.pay_registration", reg_id=registration.id, _external=True)


def send_payment_email(registration: Registration, pay_url: str) -> bool:
    """Email the member a payment link for their registration."""
    from ..models.content import get_site_settings
    conf = registration.conference
    site = get_site_settings()
    body = (
        f"Thank you for registering for {conf.title} ({conf.date_range}).\n\n"
        f"Tier: {registration.tier_name}\n"
        f"Amount: {registration.amount / 100:.2f} {(site.currency_code or 'AUD').upper()}\n\n"
        f"To complete your registration, please visit:\n{pay_url}\n"
    )
    return send_mail(
        to=registration.user.email,
        subject=f"Payment for {conf.title}",
        body=body,
    )
