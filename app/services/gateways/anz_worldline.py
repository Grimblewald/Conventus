"""ANZ Worldline Global Online Pay gateway — Hosted Checkout Page (REST API).

Credentials are stored in the PaymentGatewayConfig DB model (set via
the admin Financial panel), not in environment variables.
"""
from __future__ import annotations

import logging
import re

from flask import url_for

from . import (CheckoutResult, ConnectionTestResult, PaymentGateway,
               PaymentStatus, WebhookResult, warn_if_unreachable)
from ...models import get_active_payment_gateway

log = logging.getLogger(__name__)

_WORLDLINE_TEST_BASE = "https://payment.preprod.anzworldline-solutions.com.au"
_WORLDLINE_LIVE_BASE = "https://payment.anzworldline-solutions.com.au"

_SUCCESSFUL_EVENTS = ("payment.captured", "payment.paid")
_FAILED_EVENTS = ("payment.rejected", "payment.rejected_capture", "payment.cancelled")
_REFUND_EVENTS = ("payment.refunded", "refund.refunded")

# Matches both current references (reg_5-c2u9-a3f1) and legacy ones (reg_5).
_REG_REF = re.compile(r"^reg_(\d+)")


def _registration_reference(registration) -> str:
    """Payment-unique merchant reference, ≤30 chars (platform limit).

    Human-readable segments (registration, conference, user) plus 32 bits
    of hex so every checkout attempt gets its own reference — retries and
    double payments stay distinguishable in the ledger and Merchant Portal.
    The reg_<id> prefix (which webhooks parse to find the registration)
    partitions the space per registration, so the hex only has to be
    unique among one registration's handful of attempts — collision is
    negligible. Falls back to a shorter form if the ids are large enough
    to blow the 30-char budget.
    """
    import secrets
    suffix = secrets.token_hex(4)
    ref = (f"reg_{registration.id}-c{registration.conference_id}"
           f"u{registration.user_id}-{suffix}")
    if len(ref) > 30:
        ref = f"reg_{registration.id}-{suffix}"
    return ref[:30]


class ANZWorldlineGateway(PaymentGateway):

    @classmethod
    def checkout_origins(cls) -> list[str]:
        """Where Worldline's hosted checkout page is served from.

        A wildcard over the registrable domain rather than the two API bases
        above, because the hosted page is not always served from the API host:
        a response carrying only a partial redirect URL is assembled below into
        a deeper subdomain. Live and preprod both fall under this.
        """
        return ["https://*.anzworldline-solutions.com.au"]

    def __init__(self, config=None):
        from ...models.content import PaymentGatewayConfig
        if config is None:
            config = get_active_payment_gateway()
        self._config: PaymentGatewayConfig | None = config

    def _client(self):
        """Create a fresh SDK client from stored config."""
        if not self._config or not self._config.is_enabled:
            return None

        secret = self._config.get_api_secret()
        if not secret or not self._config.api_key_id or not self._config.merchant_id:
            return None

        base_url = _WORLDLINE_TEST_BASE if self._config.is_test_mode else _WORLDLINE_LIVE_BASE

        try:
            from onlinepayments.sdk.factory import Factory
            from onlinepayments.sdk.communicator_configuration import CommunicatorConfiguration
            from onlinepayments.sdk.authentication.authorization_type import AuthorizationType

            config = CommunicatorConfiguration(
                api_endpoint=base_url,
                api_key_id=self._config.api_key_id,
                secret_api_key=secret,
                authorization_type=AuthorizationType.V1HMAC,
                connect_timeout=10,
                socket_timeout=30,
                max_connections=10,
                integrator="Conventus",
            )
            return Factory.create_client_from_configuration(config)
        except Exception:
            log.exception("Failed to create Worldline client")
            return None

    def create_checkout(self, registration, amount: int, currency: str = "AUD",
                        return_url: str = "") -> CheckoutResult:
        # The payer is not always the member: a durable pay link is forwarded to
        # whoever settles the invoice, and they must come back to the public
        # result page rather than a login-gated member one.
        reg_id = registration.id
        return self._create_hosted_checkout(
            amount=amount,
            currency=currency,
            merchant_reference=_registration_reference(registration),
            return_url=(return_url
                        or url_for("member.pay_result", reg_id=reg_id,
                                   _external=True)),
        )

    def create_test_checkout(self, amount: int, reference: str,
                             currency: str = "AUD") -> CheckoutResult:
        """Admin-initiated test payment, not tied to any registration."""
        return self._create_hosted_checkout(
            amount=amount,
            currency=currency,
            merchant_reference=reference,
            return_url=url_for("admin.financial", _external=True),
        )

    def create_invoice_checkout(self, amount: int, reference: str,
                                return_url: str,
                                currency: str = "AUD") -> CheckoutResult:
        """Mint a fresh hosted checkout for a manual invoice's durable pay link
        (§8), reusing the invoice's own merchant reference so the capture lands
        back on the same ledger group."""
        return self._create_hosted_checkout(
            amount=amount,
            currency=currency,
            merchant_reference=reference,
            return_url=return_url,
        )

    def _create_hosted_checkout(self, amount: int, currency: str,
                                merchant_reference: str, return_url: str) -> CheckoutResult:
        client = self._client()
        if not client:
            return CheckoutResult(error="Payment gateway not configured.")

        try:
            from onlinepayments.sdk.domain.create_hosted_checkout_request import CreateHostedCheckoutRequest
            from onlinepayments.sdk.domain.order import Order
            from onlinepayments.sdk.domain.amount_of_money import AmountOfMoney
            from onlinepayments.sdk.domain.card_payment_method_specific_input import CardPaymentMethodSpecificInput
            from onlinepayments.sdk.domain.hosted_checkout_specific_input import HostedCheckoutSpecificInput
            from onlinepayments.sdk.domain.order_references import OrderReferences

            merchant_client = client.merchant(self._config.merchant_id)

            request_obj = CreateHostedCheckoutRequest()

            amount_of_money = AmountOfMoney()
            amount_of_money.currency_code = currency
            amount_of_money.amount = amount

            order = Order()
            order.amount_of_money = amount_of_money

            refs = OrderReferences()
            refs.merchant_reference = merchant_reference
            order.references = refs

            hcs_input = HostedCheckoutSpecificInput()
            hcs_input.locale = "en-AU"
            hcs_input.return_url = return_url

            # SALE authorizes and captures in one step. The default
            # (FINAL_AUTHORIZATION) only reserves the amount, leaving
            # payments stuck at pending_capture until captured manually.
            card_input = CardPaymentMethodSpecificInput()
            card_input.authorization_mode = "SALE"

            request_obj.order = order
            request_obj.card_payment_method_specific_input = card_input
            request_obj.hosted_checkout_specific_input = hcs_input

            response = merchant_client.hosted_checkout().create_hosted_checkout(request_obj)

            payment_id = response.hosted_checkout_id or ""
            redirect_url = response.redirect_url or ""

            if not redirect_url and response.partial_redirect_url:
                # The 'payment' subdomain always resolves for hosted pages.
                redirect_url = "https://payment." + response.partial_redirect_url

            if not redirect_url:
                return CheckoutResult(error="No redirect URL in Worldline response",
                                      payment_id=payment_id,
                                      merchant_reference=merchant_reference)

            # Every checkout this gateway mints passes through here, so it is
            # the one place that can notice the page being unreachable before
            # a payer does.
            warn_if_unreachable(redirect_url, "ANZWorldlineGateway")

            return CheckoutResult(redirect_url=redirect_url, payment_id=payment_id,
                                  merchant_reference=merchant_reference)

        except Exception as exc:
            log.exception("Worldline Hosted Checkout creation failed")
            return CheckoutResult(error=f"Payment service error: {exc}")

    def verify_webhook(self, request_body: bytes, headers: dict | None = None) -> WebhookResult:
        """Verify a Worldline webhook using the WebhooksHelper from the SDK.

        Callers must pass the raw POST body bytes (the HMAC is computed
        over the exact bytes received), plus the HTTP headers.
        """
        if not self._config:
            return WebhookResult(error="No payment gateway configured")

        secret = self._config.get_webhooks_secret()
        key_id = self._config.webhooks_key_id or ""

        if not secret or not key_id:
            return WebhookResult(error="Webhooks not configured")

        try:
            from onlinepayments.sdk.communication.request_header import RequestHeader
            from onlinepayments.sdk.json.default_marshaller import DefaultMarshaller
            from onlinepayments.sdk.webhooks.webhooks_helper import WebhooksHelper
            from onlinepayments.sdk.webhooks.in_memory_secret_key_store import InMemorySecretKeyStore

            key_store = InMemorySecretKeyStore()
            key_store.store_secret_key(key_id, secret)
            helper = WebhooksHelper(DefaultMarshaller.instance(), key_store)

            body = request_body if isinstance(request_body, bytes) else str(request_body).encode("utf-8")
            header_list = [RequestHeader(k, v) for k, v in (headers or {}).items()]

            event = helper.unmarshal(body, header_list)
            if not event:
                return WebhookResult(error="Could not parse webhook event")

            event_type = event.type or ""

            # Payment events carry event.payment; refund events carry
            # event.refund. Both reference the order via merchant_reference.
            if event.payment:
                transaction_id = event.payment.id or ""
                output = event.payment.payment_output
            elif event.refund:
                transaction_id = event.refund.id or ""
                output = event.refund.refund_output
            else:
                return WebhookResult(
                    error=f"Unsupported webhook event: {event_type}",
                    event_type=event_type,
                )

            ref = ""
            amount = None
            if output:
                if output.references:
                    ref = output.references.merchant_reference or ""
                if output.amount_of_money and output.amount_of_money.amount is not None:
                    amount = output.amount_of_money.amount

            reg_id = None
            m = _REG_REF.match(ref)
            if m:
                reg_id = int(m.group(1))

            common = dict(registration_id=reg_id, transaction_id=transaction_id,
                          event_type=event_type, merchant_reference=ref, amount=amount)

            if event_type in _SUCCESSFUL_EVENTS or event_type in _REFUND_EVENTS:
                return WebhookResult(success=True, **common)
            elif event_type in _FAILED_EVENTS:
                return WebhookResult(
                    success=False, error=f"Payment {event_type}", **common,
                )
            else:
                return WebhookResult(
                    success=False, error=f"Non-terminal event: {event_type}", **common,
                )

        except Exception as e:
            log.exception("Webhook verification failed")
            return WebhookResult(error=f"Webhook verification error: {e}")

    def get_payment_status(self, payment_id: str) -> PaymentStatus:
        """Fetch the current state of a payment directly from Worldline."""
        client = self._client()
        if not client:
            return PaymentStatus(error="Payment gateway not configured.")
        try:
            payment = client.merchant(self._config.merchant_id).payments().get_payment(payment_id)
            return self._payment_to_status(payment)
        except Exception as e:
            log.exception("Payment status fetch failed for %s", payment_id)
            return PaymentStatus(error=f"Status fetch failed: {e}")

    def get_checkout_payment(self, hosted_checkout_id: str) -> PaymentStatus:
        """Fetch the payment created by a hosted checkout session, if any."""
        client = self._client()
        if not client:
            return PaymentStatus(error="Payment gateway not configured.")
        try:
            resp = (client.merchant(self._config.merchant_id)
                    .hosted_checkout().get_hosted_checkout(hosted_checkout_id))
            if resp.created_payment_output and resp.created_payment_output.payment:
                return self._payment_to_status(resp.created_payment_output.payment)
            return PaymentStatus(raw_status=resp.status or "NO_PAYMENT")
        except Exception as e:
            log.exception("Checkout status fetch failed for %s", hosted_checkout_id)
            return PaymentStatus(error=f"Status fetch failed: {e}")

    def cancel_payment(self, payment_id: str) -> PaymentStatus:
        """Void an uncaptured authorization (full cancel). Returns the
        payment's resulting state."""
        client = self._client()
        if not client:
            return PaymentStatus(error="Payment gateway not configured.")
        try:
            from onlinepayments.sdk.domain.cancel_payment_request import CancelPaymentRequest
            resp = (client.merchant(self._config.merchant_id).payments()
                    .cancel_payment(payment_id, CancelPaymentRequest()))
            if resp.payment:
                return self._payment_to_status(resp.payment)
            return PaymentStatus(raw_status="CANCELLED", transaction_id=payment_id)
        except Exception as e:
            log.exception("Cancel failed for payment %s", payment_id)
            return PaymentStatus(error=f"Cancel failed: {e}")

    @staticmethod
    def _payment_to_status(payment) -> PaymentStatus:
        ref = ""
        amount = None
        output = payment.payment_output
        if output:
            if output.references:
                ref = output.references.merchant_reference or ""
            if output.amount_of_money and output.amount_of_money.amount is not None:
                amount = output.amount_of_money.amount
        return PaymentStatus(
            raw_status=payment.status or "",
            transaction_id=payment.id or "",
            merchant_reference=ref,
            amount=amount,
        )

    def test_connection(self) -> ConnectionTestResult:
        client = self._client()
        if not client:
            return ConnectionTestResult(success=False, message="Gateway not configured. Set API credentials first.")

        env = "sandbox" if self._config.is_test_mode else "live"
        base_url = _WORLDLINE_TEST_BASE if self._config.is_test_mode else _WORLDLINE_LIVE_BASE
        endpoint = base_url.removeprefix("https://")

        try:
            from onlinepayments.sdk.merchant.products.get_payment_products_params import GetPaymentProductsParams

            merchant_client = client.merchant(self._config.merchant_id)

            params = GetPaymentProductsParams()
            params.country_code = "AU"
            params.currency_code = "AUD"

            response = merchant_client.products().get_payment_products(params)
            count = len(response.payment_products) if response.payment_products else 0

            return ConnectionTestResult(
                success=True,
                message=f"Connection successful ({env}, {endpoint}). {count} payment products available.",
                details=f"Environment: {env}, Merchant: {self._config.merchant_id}"
            )
        except Exception as e:
            hint = ""
            if "ACCESS_TO_MERCHANT_NOT_ALLOWED" in str(e):
                hint = (f" Hint: the API key is valid but not for merchant "
                        f"'{self._config.merchant_id}' on the {env} environment — "
                        f"check the merchant ID matches the portal the key was "
                        f"generated in, and that the key belongs to this "
                        f"environment (sandbox keys come from the preprod portal, "
                        f"live keys from the production portal).")
            return ConnectionTestResult(
                success=False,
                message=f"Connection failed ({env}, {endpoint}): {e}{hint}",
                details=str(e)
            )
