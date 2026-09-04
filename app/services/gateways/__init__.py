"""Pluggable payment gateway interface."""
from __future__ import annotations

import fnmatch
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_DEFAULT_PORTS = {"https": 443, "http": 80}


@dataclass
class PaymentStatus:
    """Point-in-time payment state fetched from the provider (not a webhook)."""
    raw_status: str = ""            # provider status, e.g. CAPTURED / REJECTED
    transaction_id: str = ""
    merchant_reference: str = ""
    amount: int | None = None
    error: str = ""


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
    merchant_reference: str = ""
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

    @classmethod
    @abstractmethod
    def checkout_origins(cls) -> list[str]:
        """Origins this gateway sends payers to, as CSP source expressions.

        Declared by the gateway because the gateway is what redirects there.
        The site's ``form-action`` is built from this rather than repeating the
        hostname beside the policy: a browser enforces ``form-action`` on the
        redirect a form submission follows, so a copy that goes stale does not
        raise anything — it silently strands every payer on Chrome and Safari
        while the server records a checkout that looks perfectly successful.

        A classmethod: the policy is assembled at startup, before there is a
        request, a database, or a configured merchant to ask.
        """

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


def all_checkout_origins() -> list[str]:
    """Every origin any known gateway may send a payer to.

    All of them, not just the configured one: the policy is fixed at startup
    while the active gateway and its test/live mode are switched in the admin
    panel at runtime, and a policy that only covered whichever was enabled at
    boot would break the moment one was.
    """
    from .anz_worldline import ANZWorldlineGateway

    origins: list[str] = []
    for gateway in (ANZWorldlineGateway,):
        for origin in gateway.checkout_origins():
            if origin not in origins:
                origins.append(origin)
    return origins


def origin_permitted(url: str, patterns: list[str]) -> bool:
    """Whether *url*'s origin is covered by CSP-style source expressions.

    Only what this codebase actually emits is understood — a scheme, a host
    that may lead with ``*.``, and an optional port. It answers the one
    question worth asking at runtime: would a browser let a form submission
    end up here?
    """
    if not url:
        return False
    target = urlsplit(url)
    if not target.hostname:
        return False
    for pattern in patterns:
        source = urlsplit(pattern)
        if not source.hostname or source.scheme != target.scheme:
            continue
        # A source with no port means the scheme's default port, so a URL on
        # an explicit other port does not match. Erring the other way would
        # let this stay quiet about a redirect the browser is going to block.
        wanted = source.port or _DEFAULT_PORTS.get(source.scheme)
        if (target.port or _DEFAULT_PORTS.get(target.scheme)) != wanted:
            continue
        # fnmatch, so "*.example.com" matches a host at any depth beneath it —
        # which is what a browser does, and what the hosted checkout page needs
        # when it sits on a deeper subdomain than the API endpoint.
        if fnmatch.fnmatch(target.hostname.lower(), source.hostname.lower()):
            return True
    return False


def warn_if_unreachable(redirect_url: str, gateway_name: str = "") -> None:
    """Log loudly when a minted checkout sits outside the declared origins.

    The failure this guards is silent by construction: the payer sees a page
    that does nothing, and the server sees a checkout it created successfully.
    Nothing reconciles those two views, so this is the only place the
    discrepancy can be noticed at the moment it happens.
    """
    if not redirect_url or origin_permitted(redirect_url, all_checkout_origins()):
        return
    log.error(
        "Checkout redirect %s is outside the declared checkout origins %s. "
        "form-action will block it in Chrome and Safari and the payer will "
        "see nothing happen — add the origin to %s.checkout_origins().",
        urlsplit(redirect_url)._replace(path="", query="", fragment="").geturl(),
        all_checkout_origins(), gateway_name or "the gateway")
