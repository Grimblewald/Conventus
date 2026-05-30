"""Smoke tests for member blueprint."""
from __future__ import annotations


def test_dashboard_redirects_anonymous(client, seeded):
    resp = client.get("/dashboard")
    assert resp.status_code in (301, 302)  # login_required redirect
