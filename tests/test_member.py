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
