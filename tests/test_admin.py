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

    def test_a_rejected_upload_leaves_every_asset_untouched(
            self, seeded, admin_client, app):
        """A failed save must change nothing. Assets live at fixed paths
        (logo.png, signature.png), so writing them one at a time would let a
        rejected *signature* still swap the letterhead — on a save the admin
        was told had failed, with none of their text edits kept either."""
        import io
        from PIL import Image

        def png(colour):
            buf = io.BytesIO()
            Image.new("RGB", (40, 20), colour).save(buf, format="PNG")
            buf.seek(0)
            return buf

        # Establish a known-good logo first.
        admin_client.post("/admin/financial/identity", data={
            "legal_name": "Example Society Ltd",
            "logo": (png((255, 0, 0)), "logo.png"),
        }, content_type="multipart/form-data", follow_redirects=True)

        with app.app_context():
            from app.services.documents import financial_assets_dir
            logo_path = financial_assets_dir() / "logo.png"
            before = logo_path.read_bytes()

        # Now a save with a valid NEW logo but a broken signature.
        resp = admin_client.post("/admin/financial/identity", data={
            "legal_name": "Renamed By A Failed Save",
            "logo": (png((0, 0, 255)), "logo.png"),
            "signature": (io.BytesIO(b"not an image at all"), "sig.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            from app.models import get_financial_identity
            assert logo_path.read_bytes() == before, \
                "the rejected save still replaced the letterhead logo"
            # …and the text edits were not committed either.
            assert get_financial_identity().legal_name == "Example Society Ltd"

    def test_nav_links_to_every_document_editor_and_the_identity(
            self, seeded, admin_client):
        """The route split renamed the invoice-template endpoint to
        financial_document(kind); the sidebar has to follow it, and the new
        kinds and the identity page need to be reachable at all."""
        body = admin_client.get("/admin/financial/identity").data.decode()
        for href in ("/admin/financial/identity",
                     "/admin/financial/documents/invoice",
                     "/admin/financial/documents/receipt",
                     "/admin/financial/documents/adjustment"):
            assert href in body, href
        # The identity page is the current one, so its nav item is marked.
        assert 'href="/admin/financial/identity" class="active"' in body


class TestSendInvoicePreview:
    """An admin must be able to see the actual invoice before it goes out —
    previewing only the blank template is not the same document."""

    def test_preview_returns_the_invoice_pdf(self, seeded, admin_client):
        resp = admin_client.post("/admin/financial/send-invoice/preview", data={
            "to": "sponsor@example.org",
            "recipient_name": "Acme Pty Ltd",
            "tier_id": "custom",
            "item": "Sponsor package",
            "amount": "5500.00",
            "due_date": "2026-09-30",
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
            "to": "sponsor@example.org", "tier_id": "custom",
            "item": "Gold sponsorship", "amount": "100.00",
        })

        with app.app_context():
            from app.models import IssuedDocument, PaymentEvent
            assert (IssuedDocument.query.count(), PaymentEvent.query.count()) == before
        assert sent == []

    def test_preview_gst_flag_matches_the_form_not_the_identity(self, seeded, app,
                                                              monkeypatch):
        """Unticking GST on one invoice must be honoured by the preview, even
        when the society itself is GST-registered — otherwise the preview shows
        a GST breakdown the send would omit (regression)."""
        captured = {}

        def _fake_preview(kind, overrides=None, template=None):
            captured["overrides"] = overrides or {}
            return b"%PDF-preview"
        monkeypatch.setattr("app.services.documents.preview_document", _fake_preview)

        from app.blueprints.admin import financial as fin
        monkeypatch.setattr(fin, "preview_document", _fake_preview, raising=False)

        with app.app_context():
            from app.extensions import db
            from app.models import get_financial_identity
            get_financial_identity().gst_registered = True   # society IS registered
            db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            from app.models import User
            with app.app_context():
                uid = User.query.filter_by(role_name="admin").first().id
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True

        # This invoice is billed WITHOUT GST (box left unticked).
        resp = client.post("/admin/financial/send-invoice/preview", data={
            "to": "sponsor@example.org", "tier_id": "custom",
            "item": "Sponsorship", "amount": "500.00",
            # include_gst intentionally absent → GST off for this invoice
        })
        assert resp.status_code == 200
        # The decided-off flag must survive into the render, not be dropped so
        # the identity default ("1") wins.
        assert captured["overrides"].get("gst_applies") == ""

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


class TestSendInvoiceCatalogue:
    """Raising an invoice is a choice from the catalogue, not a typing exercise.

    The sender picks a conference and a sponsorship level; the description,
    line item, amount and billing period all follow from that pair, and the
    reference — which keys the ledger group, the pay link and the document's
    identity — is minted server-side and never solicited from the form.
    """

    @staticmethod
    def _conference(app, title, start, end, levels=()):
        from datetime import date
        from app.extensions import db
        from app.models import Conference
        from app.models.sponsor import SponsorTier
        import secrets
        with app.app_context():
            c = Conference(slug=f"c-{secrets.token_hex(4)}", title=title,
                           start_date=start, end_date=end)
            db.session.add(c)
            db.session.flush()
            ids = []
            for i, (name, price) in enumerate(levels):
                t = SponsorTier(conference_id=c.id, name=name,
                                display_order=i * 10, price=price)
                db.session.add(t)
                db.session.flush()
                ids.append(t.id)
            db.session.commit()
            return c.id, ids

    def test_conferences_are_ordered_by_nearness_to_today(self, seeded, app):
        """Past and future interleave: what matters is how close a meeting is
        to now, not which side of now it falls on."""
        from datetime import date, timedelta
        from app.services.invoice import invoiceable_conferences

        today = date.today()
        far_past, _ = self._conference(app, "Far past",
                                       today - timedelta(days=900),
                                       today - timedelta(days=898))
        near_past, _ = self._conference(app, "Near past",
                                        today - timedelta(days=10),
                                        today - timedelta(days=8))
        far_future, _ = self._conference(app, "Far future",
                                         today + timedelta(days=800),
                                         today + timedelta(days=802))
        near_future, _ = self._conference(app, "Near future",
                                          today + timedelta(days=5),
                                          today + timedelta(days=7))

        with app.app_context():
            order = [c.id for c in invoiceable_conferences()]

        mine = [i for i in order if i in
                {far_past, near_past, far_future, near_future}]
        assert mine[0] == near_future, "5 days away should outrank 10 days ago"
        assert mine[1] == near_past
        assert mine.index(far_future) < mine.index(far_past)

    def test_a_running_conference_sorts_first(self, seeded, app):
        from datetime import date, timedelta
        from app.services.invoice import default_conference

        today = date.today()
        self._conference(app, "Soon", today + timedelta(days=3),
                         today + timedelta(days=4))
        running, _ = self._conference(app, "Running", today - timedelta(days=1),
                                      today + timedelta(days=1))
        with app.app_context():
            assert default_conference().id == running

    def test_level_supplies_the_line_item_and_amount(self, seeded, app,
                                                     admin_client, monkeypatch):
        """The two dropdowns are the whole input — nothing else is retyped."""
        from datetime import date
        captured = {}

        def _fake_send(to, **kw):
            captured.update(kw)
            captured["to"] = to
            return True
        monkeypatch.setattr("app.services.invoice.send_manual_invoice", _fake_send)
        from app.blueprints.admin import financial as fin
        monkeypatch.setattr(fin, "send_manual_invoice", _fake_send, raising=False)

        cid, tiers = self._conference(app, "Physics 2026",
                                      date(2026, 9, 1), date(2026, 9, 3),
                                      levels=[("Gold", 500000)])
        admin_client.post("/admin/financial/send-invoice", data={
            "to": "sponsor@example.org", "conference_id": str(cid),
            "tier_id": str(tiers[0]),
        }, follow_redirects=True)

        assert captured["item"] == "Gold sponsorship"
        assert captured["amount_cents"] == 500000
        assert captured["description"] == "Sponsorship — Physics 2026"
        assert captured["period"] == "1–3 September 2026"

    def test_amount_can_be_overridden_for_a_negotiated_deal(self, seeded, app):
        from datetime import date
        from app.blueprints.admin.financial import _resolve_send_invoice

        cid, tiers = self._conference(app, "Physics 2026",
                                      date(2026, 9, 1), date(2026, 9, 3),
                                      levels=[("Gold", 500000)])
        with app.test_request_context():
            f = _resolve_send_invoice({
                "to": "s@example.org", "conference_id": str(cid),
                "tier_id": str(tiers[0]), "amount": "4200.00",
            })
        assert f["errors"] == []
        assert f["amount"] == 420000          # negotiated, not the tier price
        assert f["item"] == "Gold sponsorship"

    def test_reference_is_never_taken_from_the_form(self, seeded, app):
        """It keys the ledger group, the pay link and the document identity —
        not something to hand to whoever is raising the invoice."""
        from datetime import date
        from app.blueprints.admin.financial import _resolve_send_invoice

        cid, tiers = self._conference(app, "Physics 2026",
                                      date(2026, 9, 1), date(2026, 9, 3),
                                      levels=[("Gold", 500000)])
        with app.test_request_context():
            f = _resolve_send_invoice({
                "to": "s@example.org", "conference_id": str(cid),
                "tier_id": str(tiers[0]), "reference": "ATTACKER-CHOSEN",
            })
        assert f["reference"] != "ATTACKER-CHOSEN"
        assert f["reference"].startswith("INV-")
        assert len(f["reference"]) <= 30

    def test_references_do_not_repeat(self, seeded, app):
        from app.services.invoice import next_invoice_reference
        with app.app_context():
            refs = {next_invoice_reference() for _ in range(25)}
        assert len(refs) == 25

    def test_due_date_is_stored_as_prose_from_the_date_picker(self, seeded, app):
        """`<input type="date">` submits ISO; the document reads as prose."""
        from app.blueprints.admin.financial import _display_date
        with app.app_context():
            assert _display_date("2026-09-30") == "30 September 2026"
            assert _display_date("") == ""
            assert _display_date("whenever") == "whenever"   # never discarded

    def test_a_level_from_another_conference_is_rejected(self, seeded, app):
        """Guards the obvious tampering case and an admin with a stale form."""
        from datetime import date
        from app.blueprints.admin.financial import _resolve_send_invoice

        a_id, a_tiers = self._conference(app, "Conf A", date(2026, 9, 1),
                                         date(2026, 9, 3),
                                         levels=[("Gold", 500000)])
        b_id, _ = self._conference(app, "Conf B", date(2026, 10, 1),
                                   date(2026, 10, 3))
        with app.test_request_context():
            f = _resolve_send_invoice({
                "to": "s@example.org", "conference_id": str(b_id),
                "tier_id": str(a_tiers[0]),
            })
        assert any("does not belong" in e for e in f["errors"])

    def test_custom_invoice_needs_its_own_item(self, seeded, app):
        from datetime import date
        from app.blueprints.admin.financial import _resolve_send_invoice

        cid, _ = self._conference(app, "Physics 2026", date(2026, 9, 1),
                                  date(2026, 9, 3))
        with app.test_request_context():
            missing = _resolve_send_invoice({
                "to": "s@example.org", "conference_id": str(cid),
                "tier_id": "custom", "amount": "300.00"})
            given = _resolve_send_invoice({
                "to": "s@example.org", "conference_id": str(cid),
                "tier_id": "custom", "item": "Exhibitor booth",
                "amount": "300.00"})
        assert any("Describe the item" in e for e in missing["errors"])
        assert given["errors"] == []
        assert given["item"] == "Exhibitor booth"

    def test_a_level_with_no_price_asks_for_an_amount(self, seeded, app):
        from datetime import date
        from app.blueprints.admin.financial import _resolve_send_invoice

        cid, tiers = self._conference(app, "Physics 2026", date(2026, 9, 1),
                                      date(2026, 9, 3),
                                      levels=[("Partner", None)])
        with app.test_request_context():
            f = _resolve_send_invoice({
                "to": "s@example.org", "conference_id": str(cid),
                "tier_id": str(tiers[0])})
        assert any("no price set" in e for e in f["errors"])

    def test_form_offers_the_conference_and_level_pickers(self, seeded, app,
                                                          admin_client):
        from datetime import date
        cid, tiers = self._conference(app, "Physics 2026", date(2026, 9, 1),
                                      date(2026, 9, 3),
                                      levels=[("Gold", 500000)])
        body = admin_client.get("/admin/financial/send-invoice").data.decode()
        assert 'name="conference_id"' in body
        assert 'name="tier_id"' in body
        # Levels render server-side, so the form works without JavaScript.
        assert "Gold" in body
        # The reference is never an input.
        assert 'name="reference"' not in body
        assert 'type="date"' in body

    def test_tier_price_is_admin_only(self, seeded, app, client):
        """Prices are an invoicing fact; the public sponsor listing is logos."""
        from datetime import date
        from app.models import Conference
        cid, _ = self._conference(app, "Physics 2026", date(2026, 9, 1),
                                  date(2026, 9, 3), levels=[("Gold", 500000)])
        with app.app_context():
            slug = Conference.query.get(cid).slug
        resp = client.get(f"/conferences/{slug}")
        if resp.status_code == 200:
            assert b"5000.00" not in resp.data
