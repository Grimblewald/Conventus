"""Admin route tests: committee CRUD, user management, update page."""
from __future__ import annotations

from datetime import datetime

from app.models import CommitteeMember, User


class TestAdminAccess:
    def test_admin_page_redirects_anonymous(self, client):
        resp = client.get("/admin/")
        assert resp.status_code in (301, 302)

    def test_member_cannot_access_admin(self, member_client):
        resp = member_client.get("/admin/")
        # May be 403 (staff_required) or 302 (login_required redirect)
        # depending on whether the user_loader can resolve the session.
        assert resp.status_code in (301, 302, 403)

    def test_admin_can_access_dashboard(self, seeded, admin_client):
        resp = admin_client.get("/admin/")
        assert resp.status_code == 200


class TestCommitteeCRUD:
    def test_list_committee(self, seeded, admin_client):
        resp = admin_client.get("/admin/committee")
        assert resp.status_code == 200

    def test_create_committee_member(self, seeded, admin_client, app):
        resp = admin_client.post("/admin/committee/new", data={
            "full_name": "Dr Alice",
            "title": "Dr",
            "role": "President",
            "affiliation": "Test University",
            "display_order": "10",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Created" in resp.data or b"Alice" in resp.data

        with app.app_context():
            cm = CommitteeMember.query.filter_by(full_name="Dr Alice").first()
            assert cm is not None
            assert cm.role == "President"

    def test_create_requires_name(self, seeded, admin_client):
        resp = admin_client.post("/admin/committee/new", data={
            "full_name": "",
            "role": "Member",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Full name is required" in resp.data

    def test_edit_committee_member(self, seeded, admin_client, app):
        with app.app_context():
            cm = CommitteeMember(full_name="Bob", role="Secretary",
                                 display_order=20)
            from app.extensions import db
            db.session.add(cm)
            db.session.commit()
            mid = cm.id

        resp = admin_client.post(f"/admin/committee/{mid}/edit", data={
            "full_name": "Bob Updated",
            "title": "Prof",
            "role": "Treasurer",
            "affiliation": "New Org",
            "display_order": "5",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Saved" in resp.data or b"Updated" in resp.data

        with app.app_context():
            cm = db.session.get(CommitteeMember, mid)
            assert cm.full_name == "Bob Updated"
            assert cm.role == "Treasurer"

    def test_delete_committee_member(self, seeded, admin_client, app):
        with app.app_context():
            cm = CommitteeMember(full_name="To Delete", role="Member",
                                 display_order=99)
            from app.extensions import db
            db.session.add(cm)
            db.session.commit()
            mid = cm.id

        resp = admin_client.post(f"/admin/committee/{mid}/delete",
                                 follow_redirects=True)
        assert resp.status_code == 200
        assert b"Removed" in resp.data or b"Soft-deleted" in resp.data

        with app.app_context():
            cm = CommitteeMember.query.get(mid)
            assert cm is not None
            assert cm.deleted_at is not None

    def test_reorder_committee(self, seeded, admin_client, app):
        with app.app_context():
            from app.extensions import db
            cm1 = CommitteeMember(full_name="First", role="A",
                                  display_order=10)
            cm2 = CommitteeMember(full_name="Second", role="B",
                                  display_order=20)
            db.session.add_all([cm1, cm2])
            db.session.commit()
            id1, id2 = cm1.id, cm2.id

        resp = admin_client.post("/admin/committee/reorder", data={
            "id": [str(id2), str(id1)],
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_committee_new_page_renders(self, seeded, admin_client):
        resp = admin_client.get("/admin/committee/new")
        assert resp.status_code == 200


class TestUserManagement:
    def test_users_list(self, seeded, admin_client):
        resp = admin_client.get("/admin/users")
        assert resp.status_code == 200

    def test_users_filter_by_role(self, seeded, admin_client):
        resp = admin_client.get("/admin/users?role=member")
        assert resp.status_code == 200

    def test_set_user_role(self, seeded, admin_client, app):
        with app.app_context():
            u = User(email="roleme@test.example.org", full_name="Role Me",
                     role_name="unregistered")
            from app.extensions import db
            db.session.add(u)
            db.session.commit()
            uid = u.id

        resp = admin_client.post("/admin/users", data={
            "action": "set_role",
            "user_id": str(uid),
            "role": "committee",
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            u = db.session.get(User, uid)
            assert u.role_name == "committee"

    def test_cannot_change_admin_role(self, seeded, admin_client, app):
        with app.app_context():
            admin = User.query.filter_by(role_name="admin").first()
            uid = admin.id if admin else 1

        resp = admin_client.post("/admin/users", data={
            "action": "set_role",
            "user_id": str(uid),
            "role": "member",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"permitted" in resp.data.lower() or b"cannot" in resp.data.lower()

    def test_soft_delete_users(self, seeded, admin_client, app):
        with app.app_context():
            u = User(email="delete-me@test.example.org", full_name="Delete Me",
                     role_name="member")
            from app.extensions import db
            db.session.add(u)
            db.session.commit()
            uid = u.id

        resp = admin_client.post("/admin/users", data={
            "action": "delete",
            "user_ids": [str(uid)],
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            u = db.session.get(User, uid)
            assert u.deleted_at is not None

    def test_cannot_delete_admin(self, seeded, admin_client, app):
        with app.app_context():
            admin = User.query.filter_by(role_name="admin").first()
            uid = admin.id if admin else 1

        resp = admin_client.post("/admin/users", data={
            "action": "delete",
            "user_ids": [str(uid)],
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            from app.extensions import db
            u = db.session.get(User, uid)
            assert u.deleted_at is None


class TestFinancialInvoiceTemplate:
    def test_save_same_domain_from_email_no_warning(self, seeded, admin_client):
        """MAIL_FROM defaults to the your-domain.example.org sandbox address."""
        resp = admin_client.post("/admin/financial/documents/invoice", data={
            "subject": "Invoice", "email_body": "Body",
            "from_email": "billing@your-domain.example.org",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invoice template saved" in resp.data
        assert b"SPF/DKIM" not in resp.data

    def test_save_different_domain_from_email_warns(self, seeded, admin_client, app):
        """from_email on a domain the SMTP sender doesn't own risks SPF/DKIM
        failure — warn but still save."""
        resp = admin_client.post("/admin/financial/documents/invoice", data={
            "subject": "Invoice", "email_body": "Body",
            "from_email": "billing@some-other-domain.example.org",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invoice template saved" in resp.data
        assert b"SPF/DKIM" in resp.data

        with app.app_context():
            from app.models import get_document_template
            assert get_document_template("invoice").from_email == "billing@some-other-domain.example.org"


class TestFinancialDocumentHealth:
    """The Financial dashboard surfaces tectonic_health() loudly — plan §7/§11:
    there is no plain-format fallback, so a broken/missing tectonic must never
    be silent."""

    def test_dashboard_shows_ok_when_tectonic_present(self, seeded, admin_client):
        resp = admin_client.get("/admin/financial")
        assert resp.status_code == 200
        assert b"PDF documents" in resp.data
        assert b"tectonic ready" in resp.data
        assert b"unavailable" not in resp.data.lower()

    def test_dashboard_warns_when_tectonic_missing(self, seeded, admin_client, app):
        app.config["TECTONIC_BIN"] = "/nonexistent/tectonic-xyz"
        try:
            resp = admin_client.get("/admin/financial")
            assert resp.status_code == 200
            assert b"PDF document rendering is unavailable" in resp.data
            assert b"scripts/install-tectonic.sh" in resp.data
        finally:
            app.config.pop("TECTONIC_BIN", None)


class TestUpdatePage:
    def test_update_page_requires_system_backup(self, admin_client):
        resp = admin_client.get("/admin/update")
        assert resp.status_code in (200, 403)

    def test_member_denied_update(self, member_client):
        resp = member_client.get("/admin/update")
        assert resp.status_code == 403


class TestFinancialIdentity:
    """One issuer identity feeds every document kind: legal entity, ABN, GST,
    address, payment details, signatory, and the letterhead images."""

    def test_page_renders_and_saves(self, seeded, admin_client, app):
        resp = admin_client.get("/admin/financial/identity")
        assert resp.status_code == 200
        assert b"Financial Identity" in resp.data

        resp = admin_client.post("/admin/financial/identity", data={
            "legal_name": "Example Society Ltd",
            "abn": "17 602 379 475",
            "gst_registered": "1",
            "address": "PO Box 1\nAdelaide SA 5000",
            "contact_email": "treasurer@example.org",
            "payment_instructions": "BSB 000-000 Acct 12345678",
            "signatory_name": "Tim Barnes",
            "signatory_role": "Director/Treasurer",
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            from app.models import get_financial_identity
            ident = get_financial_identity()
            assert ident.legal_name == "Example Society Ltd"
            assert ident.abn == "17 602 379 475"
            assert ident.gst_registered is True
            assert ident.signatory_role == "Director/Treasurer"

    def test_document_variables_come_from_identity(self, seeded, app):
        with app.app_context():
            from app.extensions import db
            from app.models import get_financial_identity
            from app.services.invoice import _business_vars

            ident = get_financial_identity()
            ident.legal_name = "Example Society Ltd"
            ident.abn = "17 602 379 475"
            ident.gst_registered = True
            ident.signatory_name = "Tim Barnes"
            db.session.commit()

            v = _business_vars(11000)
            assert v["business_legal_name"] == "Example Society Ltd"
            assert v["business_number"] == "17 602 379 475"
            assert v["signatory_name"] == "Tim Barnes"
            assert v["gst_applies"] == "1"
            assert v["invoice_type"] == "Tax Invoice"
            # Amounts are cents: $110.00 inclusive → $10.00 GST, $100.00 ex.
            assert v["gst_amount"] == "10.00"
            assert v["amount_ex_gst"] == "100.00"

            # A per-send override wins over the identity's registration, and
            # a non-GST document never shows a computed zero-GST breakdown.
            v = _business_vars(11000, gst=False)
            assert v["gst_applies"] == ""
            assert v["invoice_type"] == "Invoice"

    def test_assets_are_stored_outside_the_public_uploads_tree(
            self, seeded, admin_client, app):
        """A signature is forgeable material: it must not live anywhere the
        public upload routes can reach."""
        import io
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (40, 20), (10, 10, 10)).save(buf, format="PNG")
        buf.seek(0)

        resp = admin_client.post("/admin/financial/identity", data={
            "legal_name": "Example Society Ltd",
            "signature": (buf, "sig.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            from app.models import get_financial_identity
            from app.services.documents import financial_assets_dir
            ident = get_financial_identity()
            assert ident.signature_filename == "signature.png"
            stored = financial_assets_dir() / "signature.png"
            assert stored.is_file()
            assert str(app.config["UPLOAD_FOLDER"]) not in str(stored)

        # Reachable for an admin, through the permission-gated route only.
        assert admin_client.get(
            "/admin/financial/identity/asset/signature").status_code == 200

    def test_assets_require_financial_permission(self, seeded, member_client):
        """A logged-in user without financial.manage is refused — this is the
        authorisation boundary that actually protects the signature."""
        resp = member_client.get("/admin/financial/identity/asset/signature")
        assert resp.status_code in (401, 403)
        resp = member_client.get("/admin/financial/identity")
        assert resp.status_code in (401, 403)

    def test_unknown_asset_slot_is_404(self, seeded, admin_client):
        assert admin_client.get("/admin/financial/identity/asset/passport").status_code == 404


class TestSendInvoicePreview:
    """An admin must be able to see the actual invoice before it goes out —
    previewing only the blank template is not the same document."""

    def test_preview_returns_the_invoice_pdf(self, seeded, admin_client):
        resp = admin_client.post("/admin/financial/send-invoice/preview", data={
            "to": "sponsor@example.org",
            "recipient_name": "Acme Pty Ltd",
            "description": "Gold sponsorship",
            "item": "Sponsor package",
            "amount": "5500.00",
            "reference": "INV-PREVIEW-1",
            "due_date": "30 September 2026",
            "include_gst": "1",
        })
        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        assert resp.data[:4] == b"%PDF"

    def test_preview_records_and_sends_nothing(self, seeded, admin_client, app,
                                               monkeypatch):
        sent = []
        monkeypatch.setattr("app.services.mail.send_mail",
                            lambda *a, **k: sent.append(k) or True)
        monkeypatch.setattr("app.services.invoice.send_mail",
                            lambda *a, **k: sent.append(k) or True)
        with app.app_context():
            from app.models import IssuedDocument, PaymentEvent
            before = (IssuedDocument.query.count(), PaymentEvent.query.count())

        admin_client.post("/admin/financial/send-invoice/preview", data={
            "to": "sponsor@example.org", "description": "Gold sponsorship",
            "amount": "100.00", "reference": "INV-PREVIEW-2",
        })

        with app.app_context():
            from app.models import IssuedDocument, PaymentEvent
            assert (IssuedDocument.query.count(), PaymentEvent.query.count()) == before
        assert sent == []

    def test_preview_uses_the_same_variables_as_the_send(self, seeded, app):
        """The preview and the send must resolve variables through one shared
        function, or the two drift apart the moment either changes."""
        with app.app_context():
            from app.services.invoice import manual_invoice_vars
            v = manual_invoice_vars(
                "sponsor@example.org", recipient_name="Acme Pty Ltd",
                description="Gold sponsorship", item="Sponsor package",
                amount_cents=550000, reference="INV-X", due_date="30 Sep 2026",
                recipient_address="1 Example St", include_gst=True)
            assert v["user_name"] == "Acme Pty Ltd"
            assert v["conference_title"] == "Gold sponsorship"
            assert v["transaction_id"] == "INV-X"
            assert v["recipient_address"] == "1 Example St"
            assert v["gst_applies"] == "1"
            assert v["payment_link"].endswith("/pay/invoice/INV-X")
