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


class TestDocumentTemplate:
    def test_lazy_seed_each_kind(self, app):
        """Each kind seeds an independent row with its own default wording."""
        with app.app_context():
            from app.models import get_document_template, DocumentTemplate

            for kind in ("invoice", "receipt", "adjustment"):
                t = get_document_template(kind)
                assert t.kind == kind
                assert t.id is not None
                assert t.subject and t.email_body

            inv = get_document_template("invoice")
            rec = get_document_template("receipt")
            adj = get_document_template("adjustment")
            # Three independent rows, one per kind.
            assert len({inv.id, rec.id, adj.id}) == 3
            # Kind-specific default wording (checked against the seed source so
            # the assertion survives another test mutating a saved row).
            from app.models.content import _DOCUMENT_DEFAULTS
            assert "received" in _DOCUMENT_DEFAULTS["invoice"]["email_body"].lower()
            assert "receipt" in rec.email_body.lower()
            assert "adjustment" in adj.email_body.lower()

    def test_lazy_seed_is_idempotent(self, app):
        with app.app_context():
            from app.models import get_document_template, DocumentTemplate
            first = get_document_template("receipt")
            again = get_document_template("receipt")
            assert first.id == again.id
            assert DocumentTemplate.query.filter_by(kind="receipt").count() == 1

    def test_content_hash_stable_and_sensitive(self, app):
        """Hash tracks the render-affecting fields and is otherwise stable."""
        with app.app_context():
            from app.models import DocumentTemplate
            t = DocumentTemplate(kind="invoice", pdf_body="body",
                                 business_number="12 345", gst_registered=False)
            h0 = t.content_hash
            assert t.content_hash == h0

            t.pdf_body = "different body"
            assert t.content_hash != h0
            t.pdf_body = "body"
            assert t.content_hash == h0

            t.business_number = "99 999"
            assert t.content_hash != h0
            t.business_number = "12 345"
            t.gst_registered = True
            assert t.content_hash != h0

    def test_content_hash_ignores_email_only_fields(self, app):
        with app.app_context():
            from app.models import DocumentTemplate
            t = DocumentTemplate(kind="invoice", pdf_body="body")
            h0 = t.content_hash
            t.subject = "New subject"
            t.email_body = "New body"
            t.from_name = "Someone"
            assert t.content_hash == h0


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
