"""Pluggable payment gateway interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ConnectionTestResult:
    success: bool = False
    message: str = ""
    details: str = ""


@dataclass
class CheckoutResult:
    """Result of initiating a payment checkout."""
    redirect_url: str | None = None
    payment_id: str = ""
    error: str = ""


@dataclass
class WebhookResult:
    """Outcome of verifying a webhook.

    ``merchant_reference`` is the raw order reference from the event
    (``reg_<id>`` for registrations, ``test_<token>`` for admin test
    payments); ``amount`` is the event's minor-unit amount when present.
    """
    success: bool = False
    registration_id: int | None = None
    transaction_id: str = ""
    error: str = ""
    event_type: str = ""
    merchant_reference: str = ""
    amount: int | None = None


class PaymentGateway(ABC):
    """Abstract interface for payment providers."""

    @abstractmethod
    def create_checkout(self, registration, amount: int,
                        currency: str) -> CheckoutResult:
        """Create a payment checkout session for a registration.

        Args:
            registration: Registration model instance.
            amount: Amount in minor units (e.g. cents).
            currency: 3-letter currency code.
        Returns:
            CheckoutResult with redirect_url to send the user to.
        """

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Test that API credentials are valid by making a lightweight API call."""

    @abstractmethod
    def verify_webhook(self, request_body: bytes,
                       headers: dict | None = None) -> WebhookResult:
        """Verify an incoming webhook and return the registration status.

        Args:
            request_body: Raw bytes of the webhook POST body.
            headers: HTTP headers for signature verification.
        Returns:
            WebhookResult with success status and registration_id.
        """
