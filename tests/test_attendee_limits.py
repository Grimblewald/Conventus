"""Attendee limits on registration form items.

A field ticked Limited carries max_attendees / min_attendees in the form
schema. Unticked, nothing changes. Ticked, the registration form shows the
seats left, a full item cannot be taken, and anyone already holding a seat
keeps it. A radio or select may name a value recorded instead once full, which
another field can be conditional on.
"""
from __future__ import annotations

import secrets
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Registration


@pytest.fixture(autouse=True)
def quiet_mail(monkeypatch):
    for target in ("app.services.mail.send_mail", "app.services.payments.send_mail",
                   "app.services.invoice.send_mail"):
        monkeypatch.setattr(target, lambda **kw: True)


def _dinner(**limits):
    return {"key": "attending-dinner", "label": "Attending Dinner",
            "type": "radio", "required": False, "options": ["Yes", "No"],
            **limits}


@pytest.fixture
def make_conf(app):
    from app.models import Conference
    from app.models.conference import PriceTier

    def _make(dinner: dict, *extra: dict):
        tag = secrets.token_hex(4)
        with app.app_context():
            c = Conference(slug=f"cap-{tag}", title="Capacity 2026",
                           start_date=date.today() + timedelta(days=30),
                           end_date=date.today() + timedelta(days=32),
                           is_accepting_registrations=True, is_draft=False)
            db.session.add(c)
            db.session.flush()
            db.session.add(PriceTier(conference_id=c.id, name="Free",
                                     amount=0, display_order=0))
            c.registration_form_schema = {"sections": [{"key": "p", "label": "P", "fields": [
                {"key": "tier-select", "type": "registration-tier",
                 "label": "Tier", "required": True},
                dinner, *extra]}]}
            db.session.commit()
            return {"id": c.id, "slug": c.slug}
    return _make


def _others(app, conf, n, answer="Yes", status="paid"):
    from app.models import User
    with app.app_context():
        for _ in range(n):
            u = User(email=f"{secrets.token_hex(4)}@x.example.org",
                     full_name="Other", role_name="member")
            db.session.add(u)
            db.session.flush()
            db.session.add(Registration(
                user_id=u.id, conference_id=conf["id"], tier_name="Free",
                amount=0, status=status,
                custom_data={"attending-dinner": answer}))
        db.session.commit()


def _register(client, conf, answer, **more):
    return client.post(f"/conferences/{conf['slug']}/register",
                       data={"tier": "Free", "attending-dinner": answer, **more},
                       follow_redirects=True)


def _mine(app, conf):
    from app.models import User
    with app.app_context():
        uid = User.query.filter_by(email="member@test.example.org").one().id
        return Registration.query.filter_by(conference_id=conf["id"],
                                            user_id=uid).first()


def test_no_limit_changes_nothing(seeded, member_client, app, make_conf):
    conf = make_conf(_dinner())
    _others(app, conf, 3)
    page = member_client.get(f"/conferences/{conf['slug']}/register")
    assert b"seats left" not in page.data and b"(full)" not in page.data
    _register(member_client, conf, "Yes")
    assert _mine(app, conf).custom_data["attending-dinner"] == "Yes"


def test_seats_left_are_shown(seeded, member_client, app, make_conf):
    conf = make_conf(_dinner(max_attendees=5, counts_option="Yes"))
    _others(app, conf, 2)
    _others(app, conf, 4, answer="No")
    _others(app, conf, 3, status="refunded")      # gave their seats back
    page = member_client.get(f"/conferences/{conf['slug']}/register")
    assert b"3 of 5 seats left" in page.data


def test_a_full_item_is_refused_and_the_form_kept(seeded, member_client, app,
                                                  make_conf):
    conf = make_conf(_dinner(max_attendees=2, counts_option="Yes"))
    _others(app, conf, 2)
    page = member_client.get(f"/conferences/{conf['slug']}/register")
    assert b"Fully booked" in page.data and b"Yes (full)" in page.data

    r = _register(member_client, conf, "Yes")
    assert b"Attending Dinner is full" in r.data
    assert _mine(app, conf) is None

    _register(member_client, conf, "No")           # the other answer still works
    assert _mine(app, conf).custom_data["attending-dinner"] == "No"


def test_a_seat_holder_keeps_it_when_full(seeded, member_client, app, make_conf):
    conf = make_conf(_dinner(max_attendees=2, counts_option="Yes"))
    _others(app, conf, 1)
    _register(member_client, conf, "Yes")          # takes the last seat
    assert _mine(app, conf).custom_data["attending-dinner"] == "Yes"

    page = member_client.get(f"/conferences/{conf['slug']}/register")
    assert b"Your seat is reserved" in page.data and b"(full)" not in page.data
    r = _register(member_client, conf, "Yes")      # re-saving is not refused
    assert b"is full" not in r.data


_LIMIT_KEYS = {"max_attendees", "min_attendees", "counts_option", "full_value"}


def _save(client, conf, rows: list[dict], limited: list[int]):
    """Post the form builder as the page does: one list entry per field for
    every input, and ticked Limited boxes carrying their field's position."""
    def col(name, default=""):
        return [r.get(name, default) for r in rows]
    form = {"action": "save_schema", "section_key[]": "p", "section_label[]": "P",
            "field_key_0[]": col("key"), "field_label_0[]": col("label"),
            "field_type_0[]": col("type", "text"), "field_sub_event_0[]": col("se"),
            "field_options_0[]": col("options"),
            "field_cond_field_0[]": col("cond_field"),
            "field_cond_value_0[]": col("cond_value"),
            "field_max_0[]": col("max"), "field_min_0[]": col("min"),
            "field_counts_option_0[]": col("counts"),
            "field_full_value_0[]": col("full"),
            "field_limited_0[]": [str(i) for i in limited]}
    return client.post(
        f"/admin/conferences/{conf['id']}/form-builder/registration",
        data=form, follow_redirects=True)


def _fields(app, conf):
    from app.models import Conference
    with app.app_context():
        return db.session.get(Conference, conf["id"]) \
            .registration_form_schema["sections"][0]["fields"]


_DINNER_ROW = {"key": "dinner", "label": "Dinner", "type": "radio",
               "options": "Yes\nNo", "max": "200", "full": "at-capacity"}


def test_the_builder_saves_limits_only_for_fields_ticked_limited(
        seeded, admin_client, app, make_conf):
    conf = make_conf(_dinner())
    # Limits typed into an unticked row are ignored, so unticking clears them.
    _save(admin_client, conf, [_DINNER_ROW, {**_DINNER_ROW, "key": "lunch"},
                               {"key": "notes", "label": "Notes"}], limited=[0])
    dinner, lunch, notes = _fields(app, conf)
    assert dinner["max_attendees"] == 200 and dinner["counts_option"] == "Yes"
    assert dinner["full_value"] == "at-capacity"
    assert "min_attendees" not in dinner
    assert not _LIMIT_KEYS & lunch.keys()
    assert not _LIMIT_KEYS & notes.keys()


def test_the_builder_page_hides_limits_until_ticked(seeded, admin_client, app,
                                                   make_conf):
    conf = make_conf(_dinner(), {"key": "notes", "label": "Notes", "type": "text"})
    page = admin_client.get(
        f"/admin/conferences/{conf['id']}/form-builder/registration").data
    assert page.count(b'class="js-limits" hidden') >= 2   # both rows + template
    abstract = admin_client.get(
        f"/admin/conferences/{conf['id']}/form-builder/abstract").data
    assert b'name="field_limited_' not in abstract
    assert b'class="js-limits"' not in abstract


def test_the_builder_shows_seats_taken(seeded, admin_client, app, make_conf):
    conf = make_conf(_dinner(max_attendees=10, min_attendees=5,
                             counts_option="Yes"))
    _others(app, conf, 3)
    page = admin_client.get(
        f"/admin/conferences/{conf['id']}/form-builder/registration")
    assert page.status_code == 200
    assert b"3 taken of 10" in page.data and b"7 left" in page.data
    assert b"needs 2 more to reach its minimum of 5" in page.data


@pytest.mark.parametrize("row, message", [
    ({"max": "-1", "full": ""}, b"whole numbers"),
    ({"max": "abc", "full": ""}, b"whole numbers"),
    ({"max": "", "full": ""}, b"needs a maximum, a minimum, or both"),
    ({"max": "2", "min": "5"}, b"minimum (5) is above the maximum (2)"),
    ({"full": "No"}, b"must not be one of its options"),
    ({"counts": "Maybe"}, b"must be one of its options"),
    ({"type": "checkbox", "options": ""}, b"can only be set on radio or select"),
    ({"type": "text", "options": "", "full": ""}, b"can be limited"),
])
def test_the_builder_refuses_bad_limits(seeded, admin_client, app, make_conf,
                                        row, message):
    conf = make_conf(_dinner())
    r = _save(admin_client, conf, [{**_DINNER_ROW, **row}], limited=[0])
    assert message in r.data
    assert _fields(app, conf)[-1]["key"] == "attending-dinner"   # schema stands


# --- a value recorded once full ---------------------------------------------

_INTEREST = {"key": "dinner-interest", "label": "Expression of interest",
             "type": "textarea", "required": True,
             "condition": {"field": "attending-dinner", "contains": "at-capacity"}}


def _full_conf(make_conf, app, taken=2):
    conf = make_conf(_dinner(max_attendees=2, counts_option="Yes",
                             full_value="at-capacity"), _INTEREST)
    _others(app, conf, taken)
    return conf


def test_a_full_item_records_its_full_value(seeded, member_client, app, make_conf):
    conf = _full_conf(make_conf, app)
    page = member_client.get(f"/conferences/{conf['slug']}/register").data
    assert b'type="hidden" name="attending-dinner" value="at-capacity"' in page
    assert b"Fully booked." in page and b"(full)" not in page
    assert b'value="Yes"' not in page            # nothing illegal on offer

    r = _register(member_client, conf, "at-capacity")
    assert b"Expression of interest is required" in r.data   # its condition holds
    _register(member_client, conf, "at-capacity",
              **{"dinner-interest": "Happy to join a waitlist"})
    data = _mine(app, conf).custom_data
    assert data["attending-dinner"] == "at-capacity"
    assert data["dinner-interest"] == "Happy to join a waitlist"


def test_the_full_value_takes_no_seat(seeded, member_client, app, make_conf):
    conf = _full_conf(make_conf, app, taken=1)
    _others(app, conf, 5, answer="at-capacity")
    page = member_client.get(f"/conferences/{conf['slug']}/register").data
    assert b"1 of 2 seats left" in page


def test_the_full_value_cannot_be_chosen_while_seats_remain(
        seeded, member_client, app, make_conf):
    conf = _full_conf(make_conf, app, taken=1)
    r = _register(member_client, conf, "at-capacity")
    assert b"Invalid option for Attending Dinner" in r.data
    assert _mine(app, conf) is None


def test_a_seat_asked_for_from_a_stale_form_becomes_the_full_value(
        seeded, member_client, app, make_conf):
    conf = _full_conf(make_conf, app)
    r = _register(member_client, conf, "Yes")
    assert b"Attending Dinner filled up while you were registering" in r.data
    assert b'name="attending-dinner" value="at-capacity"' in r.data
    assert _mine(app, conf) is None              # shown back, not saved


def test_no_is_kept_when_full(seeded, member_client, app, make_conf):
    conf = _full_conf(make_conf, app)
    _register(member_client, conf, "No")
    assert _mine(app, conf).custom_data["attending-dinner"] == "No"


def test_a_seat_holder_is_not_moved_to_the_full_value(
        seeded, member_client, app, make_conf):
    conf = _full_conf(make_conf, app, taken=1)
    _register(member_client, conf, "Yes")        # takes the last seat
    page = member_client.get(f"/conferences/{conf['slug']}/register").data
    assert b'value="at-capacity"' not in page and b"Your seat is reserved" in page
    _register(member_client, conf, "Yes")
    assert _mine(app, conf).custom_data["attending-dinner"] == "Yes"
