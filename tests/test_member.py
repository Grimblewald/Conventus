"""Member route tests: dashboard, profile, role promotion."""
from __future__ import annotations

from app.models import User


class TestMemberAccess:
    def test_dashboard_redirects_anonymous(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code in (301, 302)

    def test_dashboard_returns_for_member(self, member_client):
        resp = member_client.get("/dashboard")
        assert resp.status_code == 200

    def test_profile_page_renders(self, member_client):
        resp = member_client.get("/profile")
        assert resp.status_code == 200


class TestProfileEditing:
    def test_update_full_name(self, member_client, app):
        resp = member_client.post("/profile", data={
            "full_name": "Updated Name",
            "affiliation": "",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Profile saved" in resp.data

        with app.app_context():
            u = User.query.filter_by(email="member@test.example.org").first()
            assert u is not None
            assert u.full_name == "Updated Name"

    def test_update_affiliation(self, member_client, app):
        member_client.post("/profile", data={
            "full_name": "Name",
            "affiliation": "New University",
        }, follow_redirects=True)

        with app.app_context():
            u = User.query.filter_by(email="member@test.example.org").first()
            assert u is not None
            assert u.affiliation == "New University"


class TestRolePromotion:
    def test_unregistered_promoted_to_member_on_profile_completion(self, login_user_session, client, app):
        """First profile save with name should graduate unregistered → member."""
        user_id = login_user_session(email="fresh@test.example.org",
                                     full_name="", role_name="unregistered")

        with app.app_context():
            from app.extensions import db
            u = db.session.get(User, user_id)
            assert u.role_name == "unregistered"

        client.post("/profile", data={
            "full_name": "Fresh User",
            "affiliation": "",
        }, follow_redirects=True)

        with app.app_context():
            from app.extensions import db
            u = db.session.get(User, user_id)
            assert u.role_name == "member"
            assert u.full_name == "Fresh User"


class TestLogout:
    def test_member_can_logout(self, member_client, client):
        resp = member_client.get("/auth/logout", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Signed out" in resp.data

        # Subsequent dashboard access redirects.
        resp2 = member_client.get("/dashboard")
        assert resp2.status_code in (301, 302)


class TestCountryFieldRoundTrip:
    """A stored country has to survive re-opening the registration form.

    The country picker is built client-side: the server renders an empty
    <select data-country-select> and the script fills it. The stored value
    therefore travels in data-value, and the script seeds its hidden input from
    that — so if the server stopped emitting data-value, an unchanged country
    would silently submit empty and the save would fail on a missing field.
    That is exactly the bug this guards, from the server's side.
    """

    @staticmethod
    def _conf_with_country(app):
        import secrets
        from datetime import date, timedelta
        from app.extensions import db
        from app.models import Conference
        from app.models.conference import PriceTier
        tag = secrets.token_hex(4)
        today = date.today()
        with app.app_context():
            c = Conference(
                slug=f"country-{tag}", title="Physics 2026",
                start_date=today + timedelta(days=30),
                end_date=today + timedelta(days=32),
                is_accepting_registrations=True, is_draft=False,
                registration_form_schema={"sections": [{
                    "title": "About you",
                    "fields": [{"key": "country", "type": "country",
                                "label": "Country", "required": True}],
                }]},
            )
            db.session.add(c)
            db.session.flush()
            db.session.add(PriceTier(conference_id=c.id, name="Standard",
                                     amount=11000, display_order=0))
            db.session.commit()
            return c.slug

    def test_stored_country_is_offered_back_to_the_picker(self, seeded, app,
                                                          member_client):
        from app.models import Registration
        slug = self._conf_with_country(app)

        member_client.post(f"/conferences/{slug}/register",
                           data={"tier": "Standard", "country": "Australia"},
                           follow_redirects=True)
        with app.app_context():
            reg = Registration.query.order_by(Registration.id.desc()).first()
            assert reg.custom_data["country"] == "Australia"

        page = member_client.get(f"/conferences/{slug}/register")
        assert b'data-country-select' in page.data
        assert b'data-value="Australia"' in page.data

    def test_resubmitting_an_unchanged_country_is_accepted(self, seeded, app,
                                                           member_client):
        """What the browser sends once the picker seeds its hidden input."""
        from app.models import Registration
        slug = self._conf_with_country(app)

        member_client.post(f"/conferences/{slug}/register",
                           data={"tier": "Standard", "country": "Australia"},
                           follow_redirects=True)
        resp = member_client.post(f"/conferences/{slug}/register",
                                  data={"tier": "Standard",
                                        "country": "Australia",
                                        "dietary": "Vegetarian"},
                                  follow_redirects=True)
        assert b"not a recognised country" not in resp.data
        with app.app_context():
            reg = Registration.query.order_by(Registration.id.desc()).first()
            assert reg.custom_data["country"] == "Australia"
            assert reg.dietary == "Vegetarian"

    def test_an_empty_country_is_rejected_not_silently_dropped(
            self, seeded, app, member_client):
        """The failure mode the JS bug produced: field looks filled, submits
        empty. The server must say so rather than store a blank."""
        slug = self._conf_with_country(app)
        resp = member_client.post(f"/conferences/{slug}/register",
                                  data={"tier": "Standard", "country": ""},
                                  follow_redirects=True)
        assert b"required" in resp.data.lower()
