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
    """Result of processing a webhook callback."""
    success: bool = False
    registration_id: int | None = None
    transaction_id: str = ""
    error: str = ""
    event_type: str = ""


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


_gateway_registry: dict[str, type[PaymentGateway]] = {}


def register_gateway(name: str, cls: type[PaymentGateway]):
    """Register a gateway implementation."""
    _gateway_registry[name] = cls


def get_gateway(name: str) -> PaymentGateway | None:
    """Get a gateway instance by name."""
    cls = _gateway_registry.get(name)
    return cls() if cls else None
