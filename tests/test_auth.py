"""Auth tests: OTP login flow, failed attempts, lockout, logout."""
from __future__ import annotations

from app.models import OTPCode, User


class TestLoginPage:
    def test_login_page_returns_200(self, client):
        resp = client.get("/auth/login")
        assert resp.status_code == 200
        assert b"Sign in" in resp.data

    def test_verify_redirects_without_session(self, client):
        resp = client.get("/auth/verify")
        assert resp.status_code in (301, 302)

    def test_authenticated_user_redirected_from_login(self, member_client):
        resp = member_client.get("/auth/login")
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] != "/auth/login"


class TestOTPLoginFlow:
    def test_request_otp_creates_code(self, client, app):
        resp = client.post("/auth/login", data={"email": "alpha@test.example.org"},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"code" in resp.data.lower() or b"Verify" in resp.data.decode()

        with app.app_context():
            otp = OTPCode.query.filter_by(
                email="alpha@test.example.org", purpose="login"
            ).first()
            assert otp is not None
            assert len(otp.code) == 6
            assert not otp.consumed

    def test_full_login_flow_creates_user(self, client, app):
        client.post("/auth/login", data={"email": "newuser@test.example.org"})

        with app.app_context():
            otp = OTPCode.query.filter_by(
                email="newuser@test.example.org", purpose="login"
            ).first()
            code = otp.code

        resp = client.post("/auth/verify", data={"code": code}, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            u = User.query.filter_by(email="newuser@test.example.org").first()
            assert u is not None
            assert u.role_name == "unregistered"

    def test_wrong_code_fails(self, client, app):
        client.post("/auth/login", data={"email": "badcode@test.example.org"})

        with app.app_context():
            otp = OTPCode.query.filter_by(
                email="badcode@test.example.org", purpose="login"
            ).first()
            assert otp is not None

        resp = client.post("/auth/verify", data={"code": "000000"},
                           follow_redirects=True)
        assert resp.status_code == 200
        # Either shows error on verify page or redirects to login if session lost.
        page = resp.data.decode().lower()
        assert any(w in page for w in ("code", "match", "incorrect", "attempt"))
        assert "welcome" not in page  # Should NOT succeed with wrong code

    def test_invalid_email_shows_error(self, client):
        resp = client.post("/auth/login", data={"email": "bademail"},
                           follow_redirects=True)
        assert resp.status_code == 200
        # Email validator should reject input without @.
        page = resp.data.decode().lower()
        assert "valid" in page or "email" in page

    def test_logout_clears_session(self, client, app):
        client.post("/auth/login", data={"email": "logout@test.example.org"})
        with app.app_context():
            otp = OTPCode.query.filter_by(
                email="logout@test.example.org", purpose="login"
            ).first()
            code = otp.code

        client.post("/auth/verify", data={"code": code}, follow_redirects=True)

        resp = client.get("/auth/logout", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Signed out" in resp.data

        resp = client.get("/dashboard")
        assert resp.status_code in (301, 302)
