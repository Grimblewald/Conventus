"""The attendee list CSV on the registration report page: columns ticked by
an admin, saved on the conference, names sorted by family name."""
from __future__ import annotations

import csv
import io
import secrets
from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import Registration
from app.services import attendee_export as ax


@pytest.fixture
def conf(app):
    from app.models import Conference, SubEvent

    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"att-{tag}", title="Attendees 2026",
                       start_date=date.today() + timedelta(days=30),
                       end_date=date.today() + timedelta(days=32),
                       is_accepting_registrations=True, is_draft=False)
        db.session.add(c)
        db.session.flush()
        se = SubEvent(conference_id=c.id, name="Workshop", price=5000)
        db.session.add(se)
        db.session.flush()
        c.registration_form_schema = {"sections": [{"key": "p", "label": "P", "fields": [
            {"key": "given-name", "type": "text", "label": "First Name"},
            {"key": "family-name", "type": "text", "label": "Last Name"},
            {"key": "moniker", "type": "text", "label": "Preferred Name"},
            {"key": "email", "type": "email", "label": "Email Address"},
            {"key": "affiliation", "type": "text", "label": "Organisation / Institution"},
            {"key": "position-title", "type": "text", "label": "Position / Title"},
            {"key": "dietary-needs", "type": "checkbox-group", "label": "Dietary requirements",
             "options": ["None", "Vegan", "Other"]},
            {"key": "attending-dinner", "type": "radio", "label": "Attending Dinner",
             "options": ["Yes", "No"]},
            {"key": "dinner-waitlist", "type": "checkbox",
             "label": "Join the wait-list? (Dietary needs may not be met)",
             "condition": {"field": "attending-dinner", "contains": "at-capacity"}},
            {"key": "visa-letter", "type": "checkbox", "label": "Visa letter"},
            {"key": "passport-number", "type": "text", "label": "Passport number",
             "condition": {"field": "visa-letter", "value": True}},
            {"key": "tier-select", "type": "registration-tier", "label": "Tier"},
            {"key": "ws", "type": "sub-event", "label": "Workshop", "sub_event_id": se.id}]}]}
        db.session.commit()
        return {"id": c.id, "slug": c.slug}


def _reg(app, conf, given, family, *, title="", moniker="", status="paid",
         dinner="Yes", diet=("None",), workshop=None, paid=0):
    from app.models import User, record_payment_event
    with app.app_context():
        u = User(email=f"{secrets.token_hex(4)}@private.example.org",
                 full_name=f"{given} {family}", role_name="member")
        db.session.add(u)
        db.session.flush()
        reg = Registration(
            user_id=u.id, conference_id=conf["id"], tier_name="Academic", amount=50000,
            status=status, created_at=datetime(2026, 9, 1),
            custom_data={"given-name": given, "family-name": family, "moniker": moniker,
                         "email": u.email, "affiliation": "Uni of Somewhere",
                         "position-title": title, "dietary-needs": list(diet),
                         "attending-dinner": dinner, "visa-letter": False,
                         "passport-number": "LEFTOVER"},
            sub_events=({"workshop": {"attending": workshop}}
                        if workshop is not None else None))
        db.session.add(reg)
        db.session.commit()
        reg.charge_to(50000, reason="Academic")
        if paid:
            record_payment_event(registration_id=reg.id, transaction_id=f"T{reg.id}",
                                 merchant_reference=f"reg_{reg.id}",
                                 event_type="payment.captured", amount=paid)
        return reg.id


@pytest.fixture
def regs(app, conf):
    _reg(app, conf, "Zoe", "Adams", title="Associate Professor", paid=50000, workshop=True)
    _reg(app, conf, "Bob", "Brown", title="PhD student", moniker="Bobby", status="pending",
         dinner="No", diet=("Vegan", "Other"))
    _reg(app, conf, "Cy", "Clark", title="=HYPERLINK(\"x\")", status="refunded")
    return conf


def _rows(data: bytes) -> list[list[str]]:
    assert data.startswith("﻿".encode())
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


@pytest.mark.parametrize("title, expected", [
    ("Associate Professor", "A/Prof"), ("A/Prof", "A/Prof"), ("Assoc. Prof.", "A/Prof"),
    ("Professor of Pharmacy", "Prof"), ("Emeritus Professor", "Prof"), ("Prof.", "Prof"),
    ("Assistant Professor", "Asst Prof"), ("Dr.", "Dr"), ("Dr", "Dr"), ("Doctor", "Dr"),
    ("Mr.", "Mr"), ("Mrs", "Mrs"), ("Ms", "Ms"), ("Miss", "Miss"),
    ("PhD student", ""), ("Research Fellow", ""), ("Drug developer", ""),
    ("Mrsomething", ""), ("Senior Lecturer", ""), ("", ""), (None, ""),
])
def test_honorific_only_from_what_the_title_starts_with(title, expected):
    assert ax.honorific(title) == expected


def test_columns_on_offer(app, conf):
    from app.models import Conference
    with app.app_context():
        cols = ax.columns(db.session.get(Conference, conf["id"]))
    keys = [c.key for c in cols]
    assert keys[:3] == ["reference", "honorific", "badge_first_name"]
    assert "f:tier-select" not in keys and "f:ws" not in keys      # offered another way
    assert any(k.startswith("se:") for k in keys)
    groups = {c.key: c.group for c in cols}
    assert groups["f:email"] == groups["f:passport-number"] == ax.GROUP_SENSITIVE
    assert groups["f:affiliation"] == ax.GROUP_FORM


def test_suggested_columns_suit_a_badge(app, conf):
    from app.models import Conference
    with app.app_context():
        c = db.session.get(Conference, conf["id"])
        keys = ax.suggested(c)
        assert ax.saved(c) == (keys, False)
    assert keys == ["reference", "honorific", "badge_first_name", "f:given-name",
                    "f:family-name", "f:moniker", "f:affiliation", "f:position-title",
                    "tier", "payment", "f:attending-dinner", "f:dietary-needs"]


def test_the_csv(app, regs):
    from app.models import Conference
    with app.app_context():
        c = db.session.get(Conference, regs["id"])
        keys = ["honorific", "badge_first_name", "f:family-name", "payment", "owing",
                "f:attending-dinner", "f:dietary-needs", "f:passport-number",
                [k.key for k in ax.columns(c) if k.key.startswith("se:")][0], "registered"]
        data, n = ax.build_csv(c, keys)
        everyone, n_all = ax.build_csv(c, ["f:family-name", "status", "f:position-title"],
                                       include_inactive=True)
    rows = _rows(data)
    assert n == 2
    assert rows[0] == ["Honorific", "Badge first name", "Last Name", "Payment", "Amount owing",
                       "Attending Dinner", "Dietary requirements", "Passport number",
                       "Workshop", "Registered on"]
    assert rows[1] == ["A/Prof", "Zoe", "Adams", "Paid via Worldline", "", "Yes", "None", "",
                       "Attending", "2026-09-01"]
    assert rows[2] == ["", "Bobby", "Brown", "Nothing paid yet", "$500.00", "No",
                       "Vegan; Other", "", "", "2026-09-01"]
    rows = _rows(everyone)
    assert n_all == 3
    assert [r[0] for r in rows[1:]] == ["Adams", "Brown", "Clark"]
    # A typed formula is defused, not run, when the sheet is opened.
    assert rows[3] == ["Clark", "refunded", "'=HYPERLINK(\"x\")"]


def test_download_saves_the_choice_and_is_audited(seeded, admin_client, app, regs):
    from app.models import Conference
    from app.models.audit import AuditLog
    url = f"/admin/conferences/{regs['id']}/attendees.csv"
    r = admin_client.post(url, data={"col": ["f:family-name", "reference", "bogus"],
                                     "include_inactive": "1"})
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert f"attendees-{regs['slug']}-" in r.headers["Content-Disposition"]
    rows = _rows(r.data)
    assert rows[0] == ["Registration no.", "Last Name"]          # the page's order, not the post's
    assert len(rows) == 4
    with app.app_context():
        c = db.session.get(Conference, regs["id"])
        assert c.attendee_export == {"columns": ["reference", "f:family-name"],
                                     "include_inactive": True}
        assert ax.saved(c) == (["reference", "f:family-name"], True)
        log = AuditLog.query.filter_by(action="registrations.exported",
                                       target_id=str(regs["id"])).one()
        assert "3 registrations, 2 columns" in log.summary

    page = admin_client.get(f"/admin/conferences/{regs['id']}/report").data.decode()
    assert 'value="reference" checked' in page and 'value="f:family-name" checked' in page
    assert 'value="f:given-name" checked' not in page
    assert "Adams" not in page                                    # the page itself stays nameless

    r = admin_client.post(url, data={"reset": "1"})
    assert r.status_code == 302
    with app.app_context():
        c = db.session.get(Conference, regs["id"])
        assert c.attendee_export is None and ax.saved(c)[0] == ax.suggested(c)


def test_nothing_ticked_downloads_nothing(seeded, admin_client, app, regs):
    r = admin_client.post(f"/admin/conferences/{regs['id']}/attendees.csv", data={})
    assert r.status_code == 302


def test_members_cannot_download_it(seeded, member_client, regs):
    r = member_client.post(f"/admin/conferences/{regs['id']}/attendees.csv",
                           data={"col": ["f:family-name"]})
    assert r.status_code in (302, 403)
    assert b"Adams" not in r.data
