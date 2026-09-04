"""A payer can actually reach the gateway the site sends them to.

Paying is a form POST that ends on the provider's hosted page. Browsers
enforce ``form-action`` on the redirect a form submission follows — Chrome and
Safari refuse the hop, Firefox allows it — so a policy that does not name the
gateway strands most payers on a button that does nothing at all: no error, no
navigation, and a server-side log recording a checkout created successfully.

That is invisible from every side, which is why it is asserted here rather than
left to be noticed. The failure was found in production after six weeks, and
only because members wrote in.
"""
from __future__ import annotations

import pytest

from app.services.gateways import (all_checkout_origins, origin_permitted,
                                   warn_if_unreachable)
from app.services.gateways.anz_worldline import (_WORLDLINE_LIVE_BASE,
                                                 _WORLDLINE_TEST_BASE,
                                                 ANZWorldlineGateway)

LIVE_CHECKOUT = _WORLDLINE_LIVE_BASE + "/checkout/9991-abc"
# What anz_worldline.py assembles when the response carries only a partial URL.
DEEP_CHECKOUT = "https://payment.preprod.anzworldline-solutions.com.au/checkout/1"


class TestPolicyCoversTheGateway:
    def test_form_action_names_every_gateways_checkout_origins(self, app):
        """The policy is built from the gateways, so it cannot fall behind one."""
        csp = app.config.get("_TEST_CSP") or _form_action(app)
        for origin in all_checkout_origins():
            assert origin in csp, (
                f"{origin} is missing from form-action; a form POST landing "
                f"there is dropped by Chrome and Safari with no error anywhere")

    @pytest.mark.parametrize("url", [LIVE_CHECKOUT, DEEP_CHECKOUT,
                                     _WORLDLINE_TEST_BASE + "/checkout/1"])
    def test_a_real_checkout_url_is_reachable_under_the_policy(self, url):
        assert origin_permitted(url, all_checkout_origins())


class TestOriginMatching:
    def test_a_wildcard_matches_a_host_at_any_depth(self):
        """The hosted page can sit deeper than the API base it was built from."""
        pattern = ["https://*.example.com"]
        assert origin_permitted("https://payment.example.com/x", pattern)
        assert origin_permitted("https://payment.preprod.example.com/x", pattern)

    def test_it_does_not_leak_past_the_domain_or_the_scheme(self):
        pattern = ["https://*.example.com"]
        assert not origin_permitted("https://example.com.evil.test/x", pattern)
        assert not origin_permitted("http://payment.example.com/x", pattern)
        assert not origin_permitted("https://example-com.test/x", pattern)

    def test_nothing_is_not_an_origin(self):
        assert not origin_permitted("", ["https://*.example.com"])
        assert not origin_permitted("https://payment.example.com", [])


class TestTheGuardSpeaksUp:
    def test_a_checkout_outside_the_declared_origins_is_logged_as_an_error(
            self, caplog):
        """The one moment the discrepancy is visible to anyone."""
        with caplog.at_level("ERROR"):
            warn_if_unreachable("https://checkout.someone-else.test/pay",
                                "ANZWorldlineGateway")
        assert any("form-action" in r.getMessage() for r in caplog.records), (
            "an unreachable checkout must not pass in silence")

    def test_a_reachable_checkout_says_nothing(self, caplog):
        with caplog.at_level("ERROR"):
            warn_if_unreachable(LIVE_CHECKOUT, "ANZWorldlineGateway")
        assert not caplog.records

    def test_the_gateway_declares_where_it_sends_people(self):
        assert ANZWorldlineGateway.checkout_origins()
        for origin in ANZWorldlineGateway.checkout_origins():
            assert origin.startswith("https://")


def _form_action(app) -> str:
    """The form-action the app actually serves, read off a real response."""
    client = app.test_client()
    resp = client.get("/", base_url="https://localhost")
    header = resp.headers.get("Content-Security-Policy", "")
    for directive in header.split(";"):
        if directive.strip().startswith("form-action"):
            return directive
    return ""
