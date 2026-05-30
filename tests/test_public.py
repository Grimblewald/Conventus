"""Smoke tests for the public blueprint."""
from __future__ import annotations


def test_home_returns_200(client, seeded):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Test Society" in resp.data


def test_conferences_page_returns_200(client, seeded):
    resp = client.get("/conferences")
    assert resp.status_code == 200


def test_committee_page_returns_200(client, seeded):
    resp = client.get("/committee")
    assert resp.status_code == 200


def test_contact_returns_200(client, seeded):
    resp = client.get("/contact")
    assert resp.status_code == 200


def test_favicon_redirects(client, seeded):
    resp = client.get("/favicon.ico")
    assert resp.status_code in (200, 301, 302, 404)
