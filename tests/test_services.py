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

    def test_send_mail_with_attachments_console_returns_true(self, app):
        """Attachments alongside the console backend should not crash."""
        with app.app_context():
            from app.services.mail import send_mail
            ok = send_mail("test@example.org", "Subject", "Body",
                           attachments=[("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")])
            assert ok is True

    def test_send_mail_smtp_attachment_filename_and_mimetype(self, app, monkeypatch):
        """The attachment lands on the sent message with correct filename/type."""
        sent = []

        class FakeConn:
            def send_message(self, msg):
                sent.append(msg)

        monkeypatch.setenv("MAIL_BACKEND", "smtp")
        monkeypatch.setattr("app.services.mail._get_smtp_connection", lambda: FakeConn())
        with app.app_context():
            from app.services.mail import send_mail
            ok = send_mail("test@example.org", "Subject", "Body",
                           attachments=[("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")])
            assert ok is True

        parts = list(sent[0].iter_attachments())
        assert len(parts) == 1
        assert parts[0].get_filename() == "invoice.pdf"
        assert parts[0].get_content_type() == "application/pdf"

    def test_send_mail_smtp_body_is_plain_text_never_html(self, app, monkeypatch):
        """No text/html part exists on a sent message — HTML email is removed."""
        sent = []

        class FakeConn:
            def send_message(self, msg):
                sent.append(msg)

        monkeypatch.setenv("MAIL_BACKEND", "smtp")
        monkeypatch.setattr("app.services.mail._get_smtp_connection", lambda: FakeConn())
        with app.app_context():
            from app.services.mail import send_mail
            ok = send_mail("test@example.org", "Subject", "Body",
                           attachments=[("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")])
            assert ok is True

        msg = sent[0]
        assert msg.get_body(preferencelist=("plain",)) is not None
        assert msg.get_body(preferencelist=("html",)) is None
        for part in msg.walk():
            assert part.get_content_type() != "text/html"


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
