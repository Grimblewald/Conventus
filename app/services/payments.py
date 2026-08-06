"""Payment integration points.

Gateways implement the ``PaymentGateway`` ABC in
``app/services/gateways/``; the active one is selected via the
PaymentGatewayConfig DB row managed in the admin Financial panel.
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
        merchant_reference=result.merchant_reference,
        registration_id=registration.id,
        event_type="checkout.created",
        amount=registration.amount,
        note="hosted checkout session created",
    )
    return result.redirect_url


def payment_url_for(registration: Registration) -> str:
    """Return the URL a member visits to pay — always our confirmation page."""
    return url_for("member.pay_registration", reg_id=registration.id, _external=True)


def send_registration_confirmation(registration: Registration) -> bool:
    """Confirm a registration that has nothing to pay.

    Sponsors, plenary speakers and comped attendees register on a zero-amount
    tier. Sending them a payment request for $0.00 is nonsense, but sending
    nothing at all leaves them with no record that the registration landed —
    so they get the same details, minus the ask.
    """
    from ..models.content import get_site_settings
    conf = registration.conference
    site = get_site_settings()
    body = (
        f"Your registration for {conf.title} ({conf.date_range}) is confirmed.\n\n"
        f"Tier: {registration.tier_name}\n"
        f"Reference: {registration.reference}\n\n"
        f"No payment is required for this registration.\n\n"
        f"You can update your registration any time by logging in. Changes to "
        f"dietary and accessibility requirements need to reach us before we "
        f"send the final numbers to caterers and venues, so please make them "
        f"as early as you can.\n"
    )
    return send_mail(
        to=registration.user.email,
        subject=f"Registration confirmed — {conf.title}",
        body=body,
    )


def send_payment_email(registration: Registration, pay_url: str) -> bool:
    """Email the member a payment link for their registration.

    Carries the registration's reference and, if the society has configured
    payment instructions, those too. A member paying by bank transfer has
    nothing else to quote — the transfer arrives as a line on a statement, and
    without the reference on it the treasurer cannot tell whose it is.
    """
    from ..models.content import get_financial_identity, get_site_settings
    from .invoice import sanitized_reference
    conf = registration.conference
    site = get_site_settings()
    body = (
        f"Thank you for registering for {conf.title} ({conf.date_range}).\n\n"
        f"Tier: {registration.tier_name}\n"
        f"Amount: {registration.amount / 100:.2f} {(site.currency_code or 'AUD').upper()}\n"
        f"Reference: {registration.reference}\n\n"
        f"To complete your registration, please visit:\n{pay_url}\n\n"
        f"You can update your registration any time by logging in. Changes to "
        f"dietary and accessibility requirements need to reach us before we "
        f"send the final numbers to caterers and venues, so please make them "
        f"as early as you can.\n"
    )

    instructions = (get_financial_identity().payment_instructions or "").strip()
    if instructions:
        # The same variables the documents resolve, so an issuer writes their
        # EFT block once and it reads correctly wherever it is quoted.
        for name, value in (
            ("sanitized_invoice_ref", sanitized_reference(registration.reference)),
            ("payment_reference", registration.reference),
            ("transaction_id", registration.transaction_id or registration.reference),
            ("amount", f"{registration.amount / 100:.2f}"),
            ("currency_code", (site.currency_code or "AUD").upper()),
            ("currency_symbol", site.currency_symbol or ""),
            ("site_name", site.site_name),
            ("payment_link", pay_url),
        ):
            instructions = instructions.replace("{" + name + "}", str(value))
        body += f"\nOr pay by bank transfer:\n{instructions}\n"
    return send_mail(
        to=registration.user.email,
        subject=f"Payment for {conf.title}",
        body=body,
    )
