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


def initiate_payment(registration: Registration,
                     return_url: str = "") -> str | None:
    """Start a payment checkout for a registration.

    Returns a redirect URL the user should be sent to, or None if no
    gateway is configured (which means use the internal stub). *return_url*
    overrides where the payer lands afterwards, so a durable link forwarded to
    someone without an account comes back to a public page.
    """
    g = _active_gateway()
    if not g:
        return None
    from ..models.content import get_site_settings
    # The balance, not the sticker price: an upgrade on a registration that was
    # already part paid must ask for the difference, and a link clicked twice
    # must not ask for the whole fee again.
    due = registration.amount_due
    result = g.create_checkout(
        registration,
        amount=due,
        currency=(get_site_settings().currency_code or "AUD").upper(),
        return_url=return_url,
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
        amount=due,
        note="hosted checkout session created",
    )
    return result.redirect_url


def payment_url_for(registration: Registration) -> str:
    """The durable pay link for a registration — our page, never the gateway.

    Keyed on the registration's capability token rather than its id, because
    this URL is emailed and then forwarded: the person who registers is often
    not the person who pays, and a grant administrator or finance office has no
    account to log into. The token is what authorises payment, so it must not
    be guessable the way a sequential id is.
    """
    return url_for("public.pay_registration",
                   token=registration.ensure_pay_token(), _external=True)


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


def _due_date(conf) -> str:
    """When the payer needs to have paid by.

    The early-bird deadline while it stands, because that is the date the
    quoted amount depends on; the registration deadline once it has passed.
    """
    from datetime import date

    if conf is None:
        return ""
    today = date.today()
    for deadline in (conf.early_bird_deadline, conf.registration_deadline):
        if deadline and deadline >= today:
            return deadline.strftime("%-d %B %Y")
    return ""


def send_payment_email(registration: Registration) -> bool:
    """Ask a member to pay, and record that we asked. Returns whether it sent.

    Carries the registration's reference and, if the society has configured
    payment instructions, those too. A member paying by bank transfer has
    nothing else to quote — the transfer arrives as a line on a statement, and
    without the reference on it the treasurer cannot tell whose it is.

    The only way to ask, so the timestamp and count an admin reads always
    describe the same sends the payer received.
    """
    from datetime import datetime

    from ..extensions import db
    from ..models import record_payment_event
    from ..models.content import get_financial_identity, get_site_settings
    from ..models.payment_event import PAYMENT_EMAIL_EVENT
    from .invoice import _reg_merchant_reference, sanitized_reference

    pay_url = payment_url_for(registration)
    conf = registration.conference
    site = get_site_settings()
    ident = get_financial_identity()
    currency = (site.currency_code or "AUD").upper()
    due = _due_date(conf)

    body = (
        f"Thank you for registering for {conf.title} ({conf.date_range}).\n\n"
        f"Item:      {registration.tier_name}\n"
        f"Amount:    {site.currency_symbol or ''}"
        f"{registration.amount_due / 100:.2f} {currency}\n"
        f"Reference: {registration.reference}\n"
        + (f"Due:       {due}\n" if due else "")
        + f"\nPay online:\n{pay_url}\n\n"
    )

    instructions = (ident.payment_instructions or "").strip()
    if instructions:
        # Printed as written, not introduced: the issuer's block carries its own
        # wording, and a lead-in added here says the same thing a second time.
        for name, value in (
            ("sanitized_invoice_ref", sanitized_reference(registration.reference)),
            ("payment_reference", registration.reference),
            ("transaction_id", registration.transaction_id or registration.reference),
            ("amount", f"{registration.amount_due / 100:.2f}"),
            ("currency_code", currency),
            ("currency_symbol", site.currency_symbol or ""),
            ("site_name", site.site_name),
            ("payment_link", pay_url),
        ):
            instructions = instructions.replace("{" + name + "}", str(value))
        body += f"{instructions}\n\n"

    body += (
        f"Please quote {registration.reference} with your payment.\n\n"
        f"If you need an invoice as a PDF — for a grant, an employer or your "
        f"own records — you can download one from your dashboard, and a "
        f"receipt once your payment has gone through.\n\n"
        f"You can update your registration any time by logging in. Changes to "
        f"dietary and accessibility requirements need to reach us before we "
        f"send the final numbers to caterers and venues, so please make them "
        f"as early as you can.\n"
    )

    # Who is asking. Without it this is an anonymous request for a bank
    # transfer, which is the shape of a scam — and the invoice it accompanies
    # is issued by a named entity with an ABN.
    body += "\n-- \n" + "\n".join(
        line for line in (
            ident.legal_name or site.site_name,
            f"ABN {ident.abn}" if ident.abn else "",
            ident.contact_email or "",
        ) if line) + "\n"

    if not send_mail(to=registration.user.email,
                     subject=f"Payment for {conf.title}", body=body):
        return False        # A send that failed is not an ask.

    registration.payment_sent_at = datetime.utcnow()
    db.session.commit()
    record_payment_event(
        # Grouped with the payment itself rather than starting its own group.
        merchant_reference=_reg_merchant_reference(registration),
        registration_id=registration.id,
        event_type=PAYMENT_EMAIL_EVENT,
        amount=registration.amount_due,
        note=f"{registration.reference}: payment link emailed to "
             f"{registration.user.email}",
    )
    return True
