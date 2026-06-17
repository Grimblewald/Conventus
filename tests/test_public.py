"""Public route tests: home, conferences, committee, contact form with OTP."""
from __future__ import annotations

from datetime import datetime

from app.models import OTPCode, User


class TestPublicPages:
    def test_home_returns_200(self, seeded, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Test Society" in resp.data

    def test_conferences_page_returns_200(self, seeded, client):
        resp = client.get("/conferences")
        assert resp.status_code == 200

    def test_committee_page_returns_200(self, seeded, client):
        resp = client.get("/committee")
        assert resp.status_code == 200

    def test_contact_returns_200(self, seeded, client):
        resp = client.get("/contact")
        assert resp.status_code == 200

    def test_favicon_handled(self, client):
        resp = client.get("/favicon.ico")
        assert resp.status_code in (200, 301, 302, 404)


class TestContactFormOTPFlow:
    """Tests for the OTP-gated contact form."""

    ADMIN_EMAIL = "cf-admin@test.example.org"

    def _seed_admin(self, app):
        """Ensure a unique admin exists for contact form tests."""
        with app.app_context():
            admin = User.query.filter_by(email=self.ADMIN_EMAIL).first()
            if admin is None:
                from app.extensions import db
                admin = User(email=self.ADMIN_EMAIL, full_name="CF Admin",
                             role_name="admin")
                db.session.add(admin)
                db.session.commit()
            return admin.id

    def test_post_contact_issues_otp(self, seeded, client, app):
        aid = self._seed_admin(app)
        client.post("/contact", data={
            "recipient_id": str(aid),
            "name": "Alice", "email": "alice@test.example.org",
            "subject": "Hi", "message": "Hello there",
        }, follow_redirects=True)

        with app.app_context():
            otp = OTPCode.query.filter_by(
                email="alice@test.example.org", purpose="contact_form"
            ).order_by(OTPCode.id.desc()).first()
            assert otp is not None
            assert len(otp.code) == 6
            assert not otp.consumed

    def test_post_contact_redirects_to_verify(self, seeded, client, app):
        aid = self._seed_admin(app)
        resp = client.post("/contact", data={
            "recipient_id": str(aid),
            "name": "Bob", "email": "bob@test.example.org",
            "subject": "Q", "message": "Question",
        })
        assert resp.status_code == 302
        assert "/contact/verify" in resp.headers["Location"]

    def test_verify_page_shows_review(self, seeded, client, app):
        aid = self._seed_admin(app)
        client.post("/contact", data={
            "recipient_id": str(aid),
            "name": "Carol", "email": "carol@test.example.org",
            "subject": "Review", "message": "Review me",
        }, follow_redirects=True)

        resp = client.get("/contact/verify")
        assert resp.status_code == 200
        data = resp.data.decode()
        assert "Carol" in data
        assert "Review me" in data

    def test_full_contact_otp_flow(self, seeded, client, app):
        aid = self._seed_admin(app)
        client.post("/contact", data={
            "recipient_id": str(aid),
            "name": "Dan", "email": "dan@test.example.org",
            "subject": "Test", "message": "Test flow",
        }, follow_redirects=True)

        with app.app_context():
            otp = OTPCode.query.filter_by(
                email="dan@test.example.org", purpose="contact_form"
            ).order_by(OTPCode.id.desc()).first()
            assert otp is not None

        resp = client.post("/contact/verify", data={"code": otp.code},
                          follow_redirects=True)
        assert resp.status_code == 200
        assert b"Message sent" in resp.data

        # Session cleared — verify page redirects.
        resp2 = client.get("/contact/verify")
        assert resp2.status_code == 302

    def test_verify_rejects_wrong_code(self, seeded, client, app):
        aid = self._seed_admin(app)
        client.post("/contact", data={
            "recipient_id": str(aid),
            "name": "Eve", "email": "eve@test.example.org",
            "subject": "X", "message": "Wrong code",
        }, follow_redirects=True)

        resp = client.post("/contact/verify", data={"code": "000000"},
                          follow_redirects=True)
        assert resp.status_code == 200
        page = resp.data.decode().lower()
        assert any(w in page for w in ("didn't match", "expired", "code"))

    def test_resend_contact_otp(self, seeded, client, app):
        aid = self._seed_admin(app)
        client.post("/contact", data={
            "recipient_id": str(aid),
            "name": "Frank", "email": "frank@test.example.org",
            "subject": "Resend", "message": "Resend test",
        }, follow_redirects=True)

        with app.app_context():
            otp = OTPCode.query.filter_by(
                email="frank@test.example.org", purpose="contact_form"
            ).first()
            first_code = otp.code

        client.post("/contact/resend", follow_redirects=True)

        with app.app_context():
            codes = OTPCode.query.filter_by(
                email="frank@test.example.org", purpose="contact_form",
                consumed_at=None
            ).order_by(OTPCode.id.desc()).all()
            assert len(codes) >= 1
            assert codes[0].code != first_code

    def test_verify_rejects_expired_otp(self, seeded, client, app):
        aid = self._seed_admin(app)
        client.post("/contact", data={
            "recipient_id": str(aid),
            "name": "Gina", "email": "gina@test.example.org",
            "subject": "Exp", "message": "Expiry test",
        }, follow_redirects=True)

        with app.app_context():
            otp = OTPCode.query.filter_by(
                email="gina@test.example.org", purpose="contact_form"
            ).first()
            assert otp is not None
            otp.expires_at = datetime(2020, 1, 1)
            saved_code = otp.code
            from app.extensions import db
            db.session.commit()

        resp = client.post("/contact/verify", data={"code": saved_code},
                          follow_redirects=True)
        assert resp.status_code == 200
        page = resp.data.decode().lower()
        assert any(w in page for w in ("expired", "didn't match", "code"))

    def test_honeypot_silently_succeeds(self, seeded, client):
        resp = client.post("/contact", data={
            "recipient_id": "1",
            "name": "Bot", "email": "bot@test.example.org",
            "subject": "", "message": "spam",
            "confirm_human": "spam fill",
        }, follow_redirects=True)
        assert resp.status_code == 200
        page = resp.data.decode().lower()
        assert "message sent" in page or "sent" in page

    def test_contact_missing_fields(self, seeded, client):
        resp = client.post("/contact", data={
            "recipient_id": "",
            "name": "", "email": "", "subject": "", "message": "",
        }, follow_redirects=True)
        assert resp.status_code == 200
        page = resp.data.decode().lower()
        assert any(w in page for w in ("fill in", "required", "please"))

    def test_unauthenticated_verify_redirects(self, client):
        resp = client.get("/contact/verify")
        assert resp.status_code == 302
        assert "/contact" in resp.headers["Location"]

    def test_verify_redirects_when_recipient_deleted(self, seeded, client, app):
        aid = self._seed_admin(app)
        client.post("/contact", data={
            "recipient_id": str(aid),
            "name": "DeleteTest", "email": "deltest@test.example.org",
            "subject": "Del", "message": "Delete test",
        }, follow_redirects=True)

        with app.app_context():
            admin = User.query.filter_by(email=self.ADMIN_EMAIL).first()
            if admin:
                admin.deleted_at = datetime.utcnow()
                from app.extensions import db
                db.session.commit()

        resp = client.get("/contact/verify")
        assert resp.status_code == 302
