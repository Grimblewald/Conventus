"""Smoke tests for admin blueprint."""
from __future__ import annotations


def test_admin_redirects_anonymous(client, seeded):
    resp = client.get("/admin/")
    assert resp.status_code in (301, 302)  # login_required redirect
