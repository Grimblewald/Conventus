"""Stale CSRF tokens, and a refused registration keeping what was typed.

A signed CSRF token still expires after an hour. What changes is what happens
around that: open pages can fetch a fresh one, a refused post lands back on a
working page rather than a bare 400, and a registration refused for any reason
comes back filled in.
"""
from __future__ import annotations

import re
import secrets
import time
from datetime import date, timedelta

import pytest

from app.extensions import db


@pytest.fixture
def csrf_on(app, monkeypatch):
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    return app


def _token(client) -> str:
    r = client.get("/auth/csrf-token")
    assert r.status_code == 200
    return r.get_json()["token"]


def test_token_endpoint_is_private_and_uncached(csrf_on, client):
    r = client.get("/auth/csrf-token")
    assert r.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in r.headers
    assert r.get_json()["signed_in"] is False


def test_fetched_token_is_accepted(csrf_on, client, seeded):
    r = client.post("/auth/login", data={"csrf_token": _token(client),
                                          "email": "fresh@example.org"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/auth/verify")


def test_hour_old_token_is_still_refused_but_lands_on_the_form(csrf_on, client,
                                                               seeded, monkeypatch):
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() - 2 * 3600)
    old = _token(client)
    monkeypatch.setattr(time, "time", real)

    r = client.post("/auth/login", data={"csrf_token": old,
                                          "email": "late@example.org"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/auth/login")
    page = client.get(r.headers["Location"])
    assert b"open too long" in page.data


def test_missing_token_on_a_post_only_route_goes_home(csrf_on, client, seeded):
    r = client.post("/auth/resend", data={})
    assert r.status_code == 302
    assert r.headers["Location"] in ("/", "http://localhost/")


def test_refresh_interval_sits_inside_the_limit(app, client, seeded):
    page = client.get("/auth/login")
    after = int(re.search(rb'data-after-seconds="(\d+)"', page.data).group(1))
    limit = app.config.get("WTF_CSRF_TIME_LIMIT", 3600) or 3600
    assert 0 < after <= limit - 15 * 60


def _schema():
    return {"sections": [{
        "key": "about", "label": "About you", "fields": [
            {"key": "tier-select", "type": "registration-tier",
             "label": "Tier", "required": True},
            {"key": "organisation", "type": "text", "label": "Organisation",
             "required": True},
            {"key": "country", "type": "country", "label": "Country",
             "required": True},
        ]}]}


def _conference(app) -> str:
    from app.models import Conference
    from app.models.conference import PriceTier
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"keep-{tag}", title="Keep 2026",
                       start_date=date.today() + timedelta(days=30),
                       end_date=date.today() + timedelta(days=32),
                       is_accepting_registrations=True, is_draft=False,
                       registration_form_schema=_schema())
        db.session.add(c)
        db.session.flush()
        db.session.add(PriceTier(conference_id=c.id, name="Speaker", amount=0,
                                 display_order=0))
        db.session.commit()
        return c.slug


def test_refused_registration_keeps_what_was_typed(seeded, app, member_client):
    slug = _conference(app)
    r = member_client.post(f"/conferences/{slug}/register", data={
        "tier": "Speaker", "organisation": "Quotient Sciences", "country": "",
    })
    assert r.status_code == 200
    assert b"Country is required." in r.data
    assert b'value="Quotient Sciences"' in r.data
    assert re.search(rb'<option value="Speaker"\s+selected', r.data)


def test_missing_tier_keeps_what_was_typed(seeded, app, member_client):
    slug = _conference(app)
    r = member_client.post(f"/conferences/{slug}/register", data={
        "organisation": "Quotient Sciences", "country": "United Kingdom",
    })
    assert r.status_code == 200
    assert b"Please choose a registration tier." in r.data
    assert b'value="Quotient Sciences"' in r.data
    assert b'data-value="United Kingdom"' in r.data


def test_page_after_a_refused_post_carries_a_working_token(csrf_on, seeded, app,
                                                          member_client, monkeypatch):
    from app.models import Conference, Registration
    slug = _conference(app)
    form = {"tier": "Speaker", "organisation": "Quotient Sciences",
            "country": "United Kingdom"}

    # Sign an old token for this session directly; fetching one under a
    # shifted clock would also age the session cookie.
    from itsdangerous import URLSafeTimedSerializer
    _token(member_client)
    with member_client.session_transaction() as sess:
        raw = sess["csrf_token"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() - 2 * 3600)
    old = URLSafeTimedSerializer(app.secret_key, salt="wtf-csrf-token").dumps(raw)
    monkeypatch.setattr(time, "time", real)

    r = member_client.post(f"/conferences/{slug}/register",
                           data={**form, "csrf_token": old})
    assert r.status_code == 302
    page = member_client.get(r.headers["Location"])
    assert b"open too long" in page.data
    fresh = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', page.data).group(1)
    assert fresh.decode() != old

    r = member_client.post(f"/conferences/{slug}/register",
                           data={**form, "csrf_token": fresh.decode()})
    assert r.status_code == 302
    with app.app_context():
        reg = (Registration.query.join(Conference)
               .filter(Conference.slug == slug).first())
        assert reg is not None


def test_typed_country_name_saves(seeded, app, member_client):
    from app.models import Conference, Registration
    slug = _conference(app)
    member_client.post(f"/conferences/{slug}/register", data={
        "tier": "Speaker", "organisation": "Quotient Sciences",
        "country": "United Kingdom",
    })
    with app.app_context():
        reg = (Registration.query.join(Conference)
               .filter(Conference.slug == slug).first())
        assert reg.custom_data["country"] == "United Kingdom"
