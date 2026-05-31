"""Payment integration points.

This module is the documented boundary between the app and whatever
payment processor a society chooses (Stripe, PayPal, bank transfer, etc.).

What you need to implement:
  1. ``initiate_payment(registration)`` — redirect the user to a payment page
     or generate a payment link. Currently returns a URL to the internal
     stub page.
  2. ``handle_webhook(payload)`` — process asynchronous payment confirmation
     from your provider. For Stripe this is a ``checkout.session.completed``
     event. You need to:
       - Verify the webhook signature
       - Look up the Registration by ID
       - Set registration.status = "paid"
       - Call db.session.commit()
  3. ``send_payment_email(registration, payment_url)`` — send the payment
     link to the member. The default implementation uses the app's
     send_mail().

The payment_portal_enabled flag in SiteSettings controls whether any of
this activates. When disabled, registrations show a "coming soon" notice
instead.
"""
from __future__ import annotations

import logging

from flask import url_for

from ..models.registration import Registration
from .mail import send_mail

log = logging.getLogger(__name__)


def payment_url_for(registration: Registration) -> str:
    """Return the URL a member visits to pay for their registration.

    Replace this with your payment provider's checkout-session creation:
      - Stripe: create a Checkout Session, return session.url
      - PayPal: create an order, return the approval link
      - Bank transfer: return a page showing account details
    """
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


# ---------------------------------------------------------------------------
# When you integrate a real payment provider, implement these:
# ---------------------------------------------------------------------------

# def initiate_payment(registration: Registration):
#     """Create a payment session and redirect the member there.
#
#     Example for Stripe:
#
#         import stripe
#         stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
#         session = stripe.checkout.Session.create(
#             line_items=[{
#                 "price_data": {
#                     "currency": "usd",
#                     "product_data": {"name": registration.conference.title},
#                     "unit_amount": registration.amount,
#                 },
#                 "quantity": 1,
#             }],
#             mode="payment",
#             success_url=url_for("member.dashboard", _external=True),
#             cancel_url=url_for("member.pay_registration",
#                                reg_id=registration.id, _external=True),
#             metadata={"registration_id": registration.id},
#         )
#         return redirect(session.url)
#     """


# def handle_webhook(payload, signature):
#     """Process an incoming payment confirmation.
#
#     Example for Stripe:
#
#         import stripe
#         stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
#         event = stripe.Webhook.construct_event(
#             payload, signature, current_app.config["STRIPE_WEBHOOK_SECRET"]
#         )
#         if event["type"] == "checkout.session.completed":
#             session = event["data"]["object"]
#             reg_id = session["metadata"]["registration_id"]
#             reg = db.session.get(Registration, int(reg_id))
#             if reg and reg.status == "pending":
#                 reg.status = "paid"
#                 db.session.commit()
#         return {"status": "ok"}
#     """
