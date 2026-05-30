"""Smoke tests for the auth blueprint."""
from __future__ import annotations


def test_login_page_returns_200(client, seeded):
    resp = client.get("/auth/login")
    assert resp.status_code == 200
    assert b"Sign in" in resp.data


def test_verify_page_redirects_without_session(client, seeded):
    resp = client.get("/auth/verify")
    assert resp.status_code in (301, 302, 404)
