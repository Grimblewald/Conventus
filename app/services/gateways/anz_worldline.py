"""ANZ Worldline Global Online Pay gateway — Hosted Checkout Page (REST API).

Credentials are stored in the PaymentGatewayConfig DB model (set via
the admin Financial panel), not in environment variables.
"""
from __future__ import annotations

import logging
from flask import url_for

from . import CheckoutResult, ConnectionTestResult, PaymentGateway, WebhookResult, register_gateway
from ...models import get_active_payment_gateway

log = logging.getLogger(__name__)

_WORLDLINE_TEST_BASE = "https://payment.preprod.anzworldline-solutions.com.au"
_WORLDLINE_LIVE_BASE = "https://payment.anzworldline-solutions.com.au"

_SUCCESSFUL_EVENTS = ("payment.captured", "payment.paid")
_FAILED_EVENTS = ("payment.rejected", "payment.rejected_capture", "payment.cancelled")
_REFUND_EVENTS = ("payment.refunded", "refund.refunded")


class ANZWorldlineGateway(PaymentGateway):
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

    def create_checkout(self, registration, amount: int, currency: str = "AUD") -> CheckoutResult:
        reg_id = registration.id
        return self._create_hosted_checkout(
            amount=amount,
            currency=currency,
            merchant_reference=f"reg_{reg_id}",
            return_url=url_for("member.pay_result", reg_id=reg_id, _external=True),
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

    def _create_hosted_checkout(self, amount: int, currency: str,
                                merchant_reference: str, return_url: str) -> CheckoutResult:
        client = self._client()
        if not client:
            return CheckoutResult(error="Payment gateway not configured.")

        try:
            from onlinepayments.sdk.domain.create_hosted_checkout_request import CreateHostedCheckoutRequest
            from onlinepayments.sdk.domain.order import Order
            from onlinepayments.sdk.domain.amount_of_money import AmountOfMoney
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

            request_obj.order = order
            request_obj.hosted_checkout_specific_input = hcs_input

            response = merchant_client.hosted_checkout().create_hosted_checkout(request_obj)

            payment_id = response.hosted_checkout_id or ""
            redirect_url = response.redirect_url or ""

            if not redirect_url and response.partial_redirect_url:
                # The 'payment' subdomain always resolves for hosted pages.
                redirect_url = "https://payment." + response.partial_redirect_url

            if not redirect_url:
                return CheckoutResult(error="No redirect URL in Worldline response", payment_id=payment_id)

            return CheckoutResult(redirect_url=redirect_url, payment_id=payment_id)

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
            if ref.startswith("reg_"):
                try:
                    reg_id = int(ref[len("reg_"):])
                except ValueError:
                    pass

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

    def test_connection(self) -> ConnectionTestResult:
        client = self._client()
        if not client:
            return ConnectionTestResult(success=False, message="Gateway not configured. Set API credentials first.")

        try:
            from onlinepayments.sdk.merchant.products.get_payment_products_params import GetPaymentProductsParams

            merchant_client = client.merchant(self._config.merchant_id)

            params = GetPaymentProductsParams()
            params.country_code = "AU"
            params.currency_code = "AUD"

            response = merchant_client.products().get_payment_products(params)
            count = len(response.payment_products) if response.payment_products else 0

            env = "sandbox" if self._config.is_test_mode else "live"
            return ConnectionTestResult(
                success=True,
                message=f"Connection successful ({env}). {count} payment products available.",
                details=f"Environment: {env}, Merchant: {self._config.merchant_id}"
            )
        except Exception as e:
            return ConnectionTestResult(
                success=False,
                message=f"Connection failed: {e}",
                details=str(e)
            )


register_gateway("anz_worldline", ANZWorldlineGateway)
