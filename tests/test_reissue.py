"""Correcting a receipt that was sent with the wrong settings.

GST was switched on by mistake for two months, so receipts went out showing
GST the society does not charge. A correction is a new document, emailed, that
points at the one it replaces; the original stays on file. Mail is captured,
compiles are stubbed — no test here reaches a real address.
"""
from __future__ import annotations

import json
import secrets
from datetime import date
from pathlib import Path

import pytest

from app.extensions import db
from app.services import documents as docs

ADDRESS = "Level 2, 100 Example Street\nAdelaide SA 5000"
REASON = "GST was shown on the original in error. Nothing further is owed."


@pytest.fixture
def seed_templates(app):
    with app.app_context():
        from app.models import get_document_template
        for kind in ("invoice", "receipt", "adjustment"):
            get_document_template(kind)
    return app


@pytest.fixture
def mailbox(monkeypatch):
    box: list[dict] = []
    for target in ("app.services.invoice.send_mail", "app.services.mail.send_mail",
                   "app.services.payments.send_mail",
                   "app.blueprints.admin.financial.send_mail"):
        monkeypatch.setattr(target, lambda **kw: box.append(kw) or True)
    return box


@pytest.fixture
def compiled(monkeypatch):
    import hashlib
    seen: list[str] = []

    def _record(tectonic, job_dir, tex_path, epoch, memory_mb=0,
                should_abort=None, timeout=0):
        tex = Path(tex_path).read_text(encoding="utf-8")
        seen.append(tex)
        return b"%PDF-" + hashlib.sha256(tex.encode()).hexdigest().encode()

    monkeypatch.setattr(docs, "_compile", _record)
    return seen


def _set_gst(on: bool):
    from app.models import get_financial_identity
    ident = get_financial_identity()
    ident.gst_registered = on
    db.session.commit()


def _paid_reg_with_gst_receipt(app, *, address=""):
    """A paid registration whose receipt went out while GST was on."""
    from app.models import Conference, IssuedDocument, Registration, User
    from app.services.invoice import send_invoice_email
    tag = secrets.token_hex(4)
    schema = {"sections": [{"key": "p", "label": "P", "fields": [
        {"key": "bill", "type": "billing-address", "label": "Billing address"}]}]}
    with app.app_context():
        u = User(email=f"paid-{tag}@example.org", full_name="Paid Person",
                 role_name="member")
        db.session.add(u)
        db.session.flush()
        c = Conference(slug=f"re-{tag}", title="Physics 2026",
                       start_date=date(2026, 9, 1), end_date=date(2026, 9, 3),
                       registration_form_schema=schema)
        db.session.add(c)
        db.session.flush()
        reg = Registration(user_id=u.id, conference_id=c.id, tier_name="Standard",
                           amount=55000, status="paid", transaction_id=f"TXN-{tag}",
                           custom_data={"bill": address} if address else None)
        db.session.add(reg)
        db.session.commit()
        _set_gst(True)
        send_invoice_email(reg)
        _set_gst(False)
        issued = (IssuedDocument.query.filter_by(kind="receipt")
                  .order_by(IssuedDocument.id.desc()).first())
        return reg.id, issued.id, u.email


def test_the_correction_drops_gst_and_keeps_what_happened(seed_templates, mailbox,
                                                          compiled):
    from app.models import IssuedDocument
    from app.services.invoice import reissue_document
    app = seed_templates
    _rid, iid, email = _paid_reg_with_gst_receipt(app)
    with app.app_context():
        old = db.session.get(IssuedDocument, iid)
        before = json.loads(old.vars_json)
        assert before["gst_applies"] == "1"
        mailbox.clear()

        new = reissue_document(old, reason=REASON)

        after = json.loads(new.vars_json)
        assert after["gst_applies"] == ""
        for kept in ("amount", "payment_date", "transaction_id", "payment_reference",
                     "conference_title", "tier_name"):
            assert after[kept] == before[kept], kept
        assert new.replaces_id == old.id and new.reason == REASON
        assert new.reference == old.reference and new.amount == old.amount
        # The original is untouched and still there.
        assert json.loads(db.session.get(IssuedDocument, iid).vars_json) == before

    assert "Replaces the receipt issued" in compiled[-1]
    assert "\\gstfalse" in compiled[-1]
    [sent] = mailbox
    assert sent["to"] == email
    assert sent["subject"].startswith("Corrected receipt")
    assert REASON in sent["body"]
    assert sent["attachments"][0][1].startswith(b"%PDF-")


def test_it_bills_the_address_the_registration_has_now(seed_templates, mailbox,
                                                       compiled):
    from app.models import IssuedDocument, Registration
    from app.services.invoice import reissue_document
    app = seed_templates
    rid, iid, _ = _paid_reg_with_gst_receipt(app)
    with app.app_context():
        reg = db.session.get(Registration, rid)
        reg.custom_data = {"bill": ADDRESS}
        db.session.commit()
        new = reissue_document(db.session.get(IssuedDocument, iid), reason=REASON)
        assert json.loads(new.vars_json)["recipient_address"] == ADDRESS


def test_only_the_newest_version_can_be_corrected(seed_templates, mailbox, compiled):
    """A double click, or two overlapping runs, must not send twice."""
    from app.models import IssuedDocument
    from app.services.invoice import AlreadyReplaced, reissue_document
    app = seed_templates
    _rid, iid, _ = _paid_reg_with_gst_receipt(app)
    with app.app_context():
        old = db.session.get(IssuedDocument, iid)
        reissue_document(old, reason=REASON)
        mailbox.clear()
        with pytest.raises(AlreadyReplaced):
            reissue_document(old, reason=REASON)
    assert mailbox == []


def test_a_failed_send_records_nothing(seed_templates, monkeypatch, compiled):
    from app.models import IssuedDocument, PaymentEvent
    from app.services.invoice import current_document, reissue_document
    app = seed_templates
    _rid, iid, _ = _paid_reg_with_gst_receipt(app)
    monkeypatch.setattr("app.services.invoice.send_mail", lambda **kw: False)
    with app.app_context():
        old = db.session.get(IssuedDocument, iid)
        events = PaymentEvent.query.count()
        assert reissue_document(old, reason=REASON) is None
        assert current_document(old).id == iid
        assert PaymentEvent.query.count() == events


def test_the_member_download_becomes_the_correction(seed_templates, mailbox, compiled):
    from app.models import IssuedDocument, Registration
    from app.services.invoice import registration_document, reissue_document
    app = seed_templates
    rid, iid, _ = _paid_reg_with_gst_receipt(app)
    with app.app_context():
        new = reissue_document(db.session.get(IssuedDocument, iid), reason=REASON)
        pdf = registration_document(db.session.get(Registration, rid), "receipt")
        assert pdf == docs.regenerate_document(new)
        assert pdf != docs.regenerate_document(db.session.get(IssuedDocument, iid))


def test_the_ledger_notes_it_without_moving_the_balance(seed_templates, mailbox,
                                                        compiled):
    from app.models import IssuedDocument, PaymentEvent, Registration
    from app.services.invoice import reissue_document
    app = seed_templates
    rid, iid, _ = _paid_reg_with_gst_receipt(app)
    with app.app_context():
        due = db.session.get(Registration, rid).amount_due
        reissue_document(db.session.get(IssuedDocument, iid), reason=REASON)
        assert PaymentEvent.query.filter_by(
            registration_id=rid, event_type="document.reissued").count() == 1
        assert db.session.get(Registration, rid).amount_due == due


def test_a_manual_invoice_receipt_keeps_who_it_billed(seed_templates, mailbox,
                                                      compiled):
    from app.models import IssuedDocument, record_payment_event
    from app.services.invoice import (reissue_document, send_manual_invoice,
                                      send_manual_invoice_receipt)
    app = seed_templates
    ref = f"INV-{secrets.token_hex(3)}"
    with app.app_context():
        send_manual_invoice("sponsor@example.org", recipient_name="Sponsor Pty Ltd",
                            description="Gold", item="Gold", amount_cents=50000,
                            reference=ref, recipient_address=ADDRESS)
        record_payment_event(merchant_reference=ref, event_type="invoice.sent",
                             amount=50000, note="to sponsor@example.org")
        _set_gst(True)
        send_manual_invoice_receipt(ref, amount_cents=50000, transaction_id="T1")
        _set_gst(False)
        receipt = (IssuedDocument.query.filter_by(kind="receipt", reference=ref)
                   .first())
        mailbox.clear()
        new = reissue_document(receipt, reason=REASON)
        after = json.loads(new.vars_json)
        assert after["user_name"] == "Sponsor Pty Ltd"
        assert after["recipient_address"] == ADDRESS
        assert after["gst_applies"] == ""
    assert mailbox[0]["to"] == "sponsor@example.org"


def test_old_documents_still_rebuild_without_the_note(seed_templates, mailbox,
                                                      compiled):
    from app.models import IssuedDocument
    app = seed_templates
    _rid, iid, _ = _paid_reg_with_gst_receipt(app)
    with app.app_context():
        docs.regenerate_document(db.session.get(IssuedDocument, iid))
    assert "\\replacesfalse" in compiled[-1]
    assert "Replaces the" not in compiled[-1]


# --- every version stays visible ----------------------------------------------

def _login_as_owner(app, client, rid):
    from app.models import Registration
    with app.app_context():
        uid = db.session.get(Registration, rid).user_id
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


class TestHistory:
    def test_the_member_sees_and_can_fetch_every_version(self, seed_templates,
                                                         client, mailbox, compiled):
        from app.models import IssuedDocument
        from app.services.invoice import reissue_document
        app = seed_templates
        rid, iid, _ = _paid_reg_with_gst_receipt(app)
        with app.app_context():
            new = reissue_document(db.session.get(IssuedDocument, iid), reason=REASON)
            new_id = new.id
        _login_as_owner(app, client, rid)

        page = client.get("/dashboard").get_data(as_text=True)
        assert "Receipts sent (2)" in page
        assert "— current" in page and "— replaced" in page
        assert REASON in page

        old_pdf = client.get(f"/registrations/{rid}/documents/{iid}")
        new_pdf = client.get(f"/registrations/{rid}/documents/{new_id}")
        assert old_pdf.mimetype == new_pdf.mimetype == "application/pdf"
        assert old_pdf.data != new_pdf.data

    def test_another_member_cannot_fetch_them(self, seed_templates, member_client,
                                              mailbox, compiled):
        app = seed_templates
        rid, iid, _ = _paid_reg_with_gst_receipt(app)
        assert member_client.get(
            f"/registrations/{rid}/documents/{iid}").status_code == 403

    def test_a_document_of_another_registration_is_not_reachable(
            self, seed_templates, client, mailbox, compiled):
        app = seed_templates
        rid, _iid, _ = _paid_reg_with_gst_receipt(app)
        _other_rid, other_iid, _ = _paid_reg_with_gst_receipt(app)
        _login_as_owner(app, client, rid)
        assert client.get(
            f"/registrations/{rid}/documents/{other_iid}").status_code == 404

    def test_the_admin_page_lists_them(self, seed_templates, admin_client, mailbox,
                                       compiled):
        from app.models import IssuedDocument
        from app.services.invoice import reissue_document
        app = seed_templates
        rid, iid, _ = _paid_reg_with_gst_receipt(app)
        with app.app_context():
            reissue_document(db.session.get(IssuedDocument, iid), reason=REASON)
        page = admin_client.get(f"/admin/registrations/{rid}").get_data(as_text=True)
        assert "Documents sent" in page
        assert page.count("Receipt issued") == 2


# --- the admin tool -----------------------------------------------------------

def _code(mailbox) -> str:
    import re
    return re.search(r"Confirmation code: (\d{6})", mailbox[-1]["body"]).group(1)


class TestAdminTool:
    def test_it_lists_receipts_showing_gst(self, seed_templates, admin_client,
                                           mailbox, compiled):
        app = seed_templates
        _rid, iid, email = _paid_reg_with_gst_receipt(app)
        page = admin_client.get("/admin/financial/reissue?gst=shown")
        assert f'name="doc_id" value="{iid}"'.encode() in page.data
        assert email.encode() in page.data

    def test_preview_sends_and_records_nothing(self, seed_templates, admin_client,
                                               mailbox, compiled):
        from app.models import IssuedDocument
        app = seed_templates
        _rid, iid, _ = _paid_reg_with_gst_receipt(app)
        mailbox.clear()
        with app.app_context():
            before = IssuedDocument.query.count()
        resp = admin_client.get(f"/admin/financial/reissue/{iid}/preview")
        assert resp.mimetype == "application/pdf"
        assert "Replaces the receipt issued" in compiled[-1]
        assert mailbox == []
        with app.app_context():
            assert IssuedDocument.query.count() == before

    def test_nothing_reaches_registrants_without_the_code(self, seed_templates,
                                                          admin_client, mailbox,
                                                          compiled):
        app = seed_templates
        _rid, iid, email = _paid_reg_with_gst_receipt(app)
        mailbox.clear()
        admin_client.post("/admin/financial/reissue/request",
                          data={"doc_id": [str(iid)], "reason": REASON})
        assert [m["to"] for m in mailbox] == ["admin@test.example.org"]
        assert "Confirm reissuing" in mailbox[0]["subject"]

        page = admin_client.post("/admin/financial/reissue/confirm",
                                 data={"code": "000000"}, follow_redirects=True)
        assert b"match, or it has expired" in page.data
        assert all(m["to"] != email for m in mailbox)

    def test_the_right_code_reissues_and_emails(self, seed_templates, admin_client,
                                                mailbox, compiled):
        from app.models import IssuedDocument
        app = seed_templates
        _rid, iid, email = _paid_reg_with_gst_receipt(app)
        mailbox.clear()   # the original receipt, sent while setting up
        admin_client.post("/admin/financial/reissue/request",
                          data={"doc_id": [str(iid)], "reason": REASON})
        page = admin_client.get("/admin/financial/reissue/confirm")
        assert b"cannot be unsent" in page.data and email.encode() in page.data

        admin_client.post("/admin/financial/reissue/confirm",
                          data={"code": _code(mailbox)}, follow_redirects=True)
        to_member = [m for m in mailbox if m["to"] == email]
        assert len(to_member) == 1 and REASON in to_member[0]["body"]
        with app.app_context():
            assert IssuedDocument.query.filter_by(replaces_id=iid).count() == 1
        # Corrected, so it no longer shows GST and drops off the list.
        page = admin_client.get("/admin/financial/reissue?gst=shown")
        assert f'name="doc_id" value="{iid}"'.encode() not in page.data

    def test_a_used_code_cannot_send_again(self, seed_templates, admin_client,
                                           mailbox, compiled):
        app = seed_templates
        _rid, iid, email = _paid_reg_with_gst_receipt(app)
        admin_client.post("/admin/financial/reissue/request",
                          data={"doc_id": [str(iid)], "reason": REASON})
        code = _code(mailbox)
        admin_client.post("/admin/financial/reissue/confirm", data={"code": code})
        sent = len([m for m in mailbox if m["to"] == email])
        admin_client.post("/admin/financial/reissue/confirm", data={"code": code})
        assert len([m for m in mailbox if m["to"] == email]) == sent

    def test_members_cannot_reach_it(self, seeded, member_client):
        assert member_client.get("/admin/financial/reissue").status_code == 403
        assert member_client.post("/admin/financial/reissue/request",
                                  data={"doc_id": ["1"], "reason": "x"}).status_code == 403
