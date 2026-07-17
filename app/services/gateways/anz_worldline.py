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
        from onlinepayments.sdk.factory import Factory
        from onlinepayments.sdk.communicator_configuration import CommunicatorConfiguration
        from onlinepayments.sdk.default_connection import DefaultConnection
        from onlinepayments.sdk.metadata_provider import MetadataProviderBuilder

        secret = self._config.get_api_secret()
        if not secret or not self._config.api_key_id or not self._config.merchant_id:
            return None

        base_url = _WORLDLINE_TEST_BASE if self._config.is_test_mode else _WORLDLINE_LIVE_BASE

        try:
            config = CommunicatorConfiguration(
                api_key=self._config.api_key_id,
                api_secret=secret,
                api_endpoint=base_url,
                integrator="Conventus"
            )
            conn = DefaultConnection()
            meta_provider = MetadataProviderBuilder().with_integrator("Conventus").build()
            client = Factory.create_client_from_configuration(config, conn=conn, metadata_provider=meta_provider)
            return client
        except Exception as e:
            log.exception("Failed to create Worldline client")
            return None

    def create_checkout(self, registration, amount: int, currency: str = "AUD") -> CheckoutResult:
        client = self._client()
        if not client:
            return CheckoutResult(error="Payment gateway not configured.")

        from onlinepayments.sdk.domain.create_hosted_checkout_request import CreateHostedCheckoutRequest
        from onlinepayments.sdk.domain.order import Order
        from onlinepayments.sdk.domain.amount_of_money import AmountOfMoney
        from onlinepayments.sdk.domain.hosted_checkout_specific_input import HostedCheckoutSpecificInput

        reg_id = registration.id
        return_url = url_for("member.pay_result", reg_id=reg_id, _external=True)

        try:
            merchant_client = client.merchant(self._config.merchant_id)

            request_obj = CreateHostedCheckoutRequest()

            amount_of_money = AmountOfMoney()
            amount_of_money.currency_code = currency
            amount_of_money.amount = amount

            order = Order()
            order.amount_of_money = amount_of_money

            hcs_input = HostedCheckoutSpecificInput()
            hcs_input.locale = "en-AU"
            hcs_input.return_url = return_url
            hcs_input.variant = ""

            request_obj.order = order
            request_obj.hosted_checkout_specific_input = hcs_input

            response = merchant_client.hosted_checkout().create_hosted_checkout(request_obj)

            payment_id = response.hosted_checkout_id or ""
            redirect_url = response.redirect_url or ""

            if not redirect_url and response.partial_redirect_url:
                base = _WORLDLINE_TEST_BASE if self._config.is_test_mode else _WORLDLINE_LIVE_BASE
                redirect_url = "https://payment." + response.partial_redirect_url

            if not redirect_url:
                return CheckoutResult(error="No redirect URL in Worldline response", payment_id=payment_id)

            return CheckoutResult(redirect_url=redirect_url, payment_id=payment_id)

        except Exception as exc:
            log.exception("Worldline Hosted Checkout creation failed")
            return CheckoutResult(error=f"Payment service error: {exc}")

    def verify_webhook(self, request_body: bytes, headers: dict | None = None) -> WebhookResult:
        """Verify a Worldline webhook using the WebhooksHelper from the SDK.

        NOTE: Signature changed from (request_data: dict, headers: dict)
        to (request_body: bytes, headers: dict | None = None).  Callers
        must pass the raw POST body bytes, not parsed JSON.
        """
        if not self._config:
            return WebhookResult(error="No payment gateway configured")

        secret = self._config.get_webhooks_secret()
        key_id = self._config.webhooks_key_id or ""

        if not secret or not key_id:
            return WebhookResult(error="Webhooks not configured")

        from onlinepayments.sdk.webhooks.webhooks_helper import WebhooksHelper
        from onlinepayments.sdk.webhooks.in_memory_secret_key_store import InMemorySecretKeyStore

        key_store = InMemorySecretKeyStore()
        key_store.store_secret_key(key_id, secret)

        helper = WebhooksHelper(key_store)

        try:
            header_list = []
            if headers:
                for k, v in headers.items():
                    header_list.append({"name": k, "value": v})

            try:
                body_str = request_body.decode("utf-8") if isinstance(request_body, bytes) else request_body
            except Exception:
                body_str = request_body

            event = helper.unmarshal(body_str, header_list)

            if not event or not event.payment:
                return WebhookResult(error="Could not parse webhook event")

            reg_id = None
            if event.payment.payment_output and event.payment.payment_output.references:
                ref = event.payment.payment_output.references.merchant_reference or ""
                ref_parts = ref.split("reg_")
                if len(ref_parts) == 2:
                    try:
                        reg_id = int(ref_parts[1])
                    except ValueError:
                        pass

            event_type = event.type or ""
            transaction_id = event.payment.id or ""

            successful_events = ("payment.captured", "payment.paid")
            failed_events = ("payment.rejected", "payment.cancelled")
            refunded_events = ("payment.refunded", "refund.refunded")

            if event_type in successful_events:
                return WebhookResult(success=True, registration_id=reg_id, transaction_id=transaction_id)
            elif event_type in refunded_events:
                return WebhookResult(success=True, registration_id=reg_id, transaction_id=transaction_id)
            elif event_type in failed_events:
                return WebhookResult(success=False, registration_id=reg_id, transaction_id=transaction_id, error=f"Payment {event_type}")
            else:
                return WebhookResult(success=False, registration_id=reg_id, transaction_id=transaction_id, error=f"Non-terminal event: {event_type}")

        except Exception as e:
            log.exception("Webhook verification failed")
            return WebhookResult(error=f"Webhook verification error: {e}")

    def test_connection(self) -> ConnectionTestResult:
        client = self._client()
        if not client:
            return ConnectionTestResult(success=False, message="Gateway not configured. Set API credentials first.")

        try:
            merchant_client = client.merchant(self._config.merchant_id)

            from onlinepayments.sdk.merchant.products.get_payment_products_params import GetPaymentProductsParams
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
