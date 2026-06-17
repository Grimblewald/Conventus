"""Service tests: mail backend, send_mail behavior."""
from __future__ import annotations


class TestMailService:
    def test_send_mail_console_returns_true(self, app):
        """MAIL_BACKEND=console always returns True."""
        with app.app_context():
            from app.services.mail import send_mail
            ok = send_mail("test@example.org", "Subject", "Body")
            assert ok is True

    def test_send_mail_with_sender_name(self, app):
        """Passing sender_name should not crash."""
        with app.app_context():
            from app.services.mail import send_mail
            ok = send_mail("test@example.org", "Subject", "Body",
                           sender_name="Test Sender")
            assert ok is True

    def test_send_mail_with_reply_to(self, app):
        with app.app_context():
            from app.services.mail import send_mail
            ok = send_mail("test@example.org", "Subject", "Body",
                           reply_to="reply@example.org")
            assert ok is True

    def test_send_mail_all_params(self, app):
        with app.app_context():
            from app.services.mail import send_mail
            ok = send_mail("test@example.org", "Subject", "Body",
                           sender_name="Contact Form",
                           reply_to="Jane Doe <jane@example.org>")
            assert ok is True


class TestAdminCLI:
    def test_ensure_roles_exist_idempotent(self, app):
        with app.app_context():
            from app.models.user import ensure_roles_exist
            from app.models import Role

            # First call.
            ensure_roles_exist()
            roles = Role.query.all()
            assert len(roles) == 4

            # Second call should not create duplicates.
            ensure_roles_exist()
            roles2 = Role.query.all()
            assert len(roles2) == 4
