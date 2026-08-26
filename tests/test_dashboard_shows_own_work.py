"""A member's own registration and abstract appear on their dashboard.

Reported: signed in, both rows present in the database, dashboard showing
neither. The dashboard filters on the signed-in user's id and a soft-delete
flag, so this walks the routes a member actually uses and then reads the page
back, rather than asserting on the query in isolation.
"""
from __future__ import annotations

import secrets
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Abstract, Conference, Registration
from app.models.conference import PriceTier


@pytest.fixture
def conference(app):
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"dash-{tag}", title="Dashboard Conference",
                       start_date=date.today() + timedelta(days=120),
                       end_date=date.today() + timedelta(days=122),
                       abstract_deadline=date.today() + timedelta(days=30),
                       registration_deadline=date.today() + timedelta(days=60))
        db.session.add(c)
        db.session.flush()
        db.session.add(PriceTier(conference_id=c.id, name="Student",
                                 amount=15000))
        db.session.commit()
        return c.slug


class TestOwnWorkIsListed:
    def test_a_registration_made_through_the_form_is_listed(
            self, seeded, member_client, app, conference):
        resp = member_client.post(f"/conferences/{conference}/register",
                                  data={"tier": "Student"},
                                  follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            assert Registration.query.filter_by(deleted_at=None).count() >= 1

        page = member_client.get("/dashboard")
        body = page.get_data(as_text=True)
        assert page.status_code == 200
        assert "You haven't registered for any conferences yet." not in body
        assert "Dashboard Conference" in body

    def test_an_abstract_submitted_through_the_form_is_listed(
            self, seeded, member_client, app, conference):
        resp = member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "Sustained antibiotic delivery",
            "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing to complain about.",
            "presenting_author_index": "0", "action": "submit",
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            assert Abstract.query.filter_by(deleted_at=None).count() >= 1

        page = member_client.get("/dashboard")
        body = page.get_data(as_text=True)
        assert page.status_code == 200
        assert "No abstracts submitted yet." not in body
        assert "Sustained antibiotic delivery" in body

    def test_both_together_render_the_page_the_member_sees(
            self, seeded, member_client, app, conference):
        """The reported shape: one of each, on one dashboard."""
        member_client.post(f"/conferences/{conference}/register",
                           data={"tier": "Student"}, follow_redirects=True)
        member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "Sustained antibiotic delivery",
            "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing to complain about.",
            "presenting_author_index": "0", "action": "submit",
        }, follow_redirects=True)

        page = member_client.get("/dashboard")
        body = page.get_data(as_text=True)
        assert page.status_code == 200, body[:2000]
        assert "Dashboard Conference" in body
        assert "Sustained antibiotic delivery" in body
