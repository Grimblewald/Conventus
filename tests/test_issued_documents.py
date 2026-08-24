"""Regeneration store tests (plan build step 7 / §12, ATO 5-year retention).

Every real send that attaches a PDF appends an IssuedDocument snapshot from
which the exact PDF can be rebuilt byte-identically; test invoices and previews
never do. Regeneration renders through the ONE renderer using the stored vars
and a template stand-in, so it stays faithful even after the live template is
edited. Compiles are stubbed at the pure `_compile` step — no real tectonic.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest

from app.extensions import db
from app.services import documents as docs


# --- fixtures ----------------------------------------------------------------

@pytest.fixture
def seed_templates(app):
    with app.app_context():
        from app.models import get_document_template
        for kind in ("invoice", "receipt", "adjustment"):
            get_document_template(kind)
    return app


@pytest.fixture
def mailbox(monkeypatch):
    """Capture every send so nothing tries to leave the process; always OK."""
    box: list[dict] = []
    monkeypatch.setattr("app.services.invoice.send_mail",
                        lambda **kw: box.append(kw) or True)
    monkeypatch.setattr("app.services.mail.send_mail",
                        lambda **kw: box.append(kw) or True)
    return box


def _content_compile(tectonic, job_dir, tex_path, epoch, memory_mb=0,
                     should_abort=None, timeout=0):
    """A content-sensitive fake compile: identical .tex → identical bytes,
    different .tex → different bytes. Lets the determinism test prove the PDF is
    rebuilt from the snapshot, not from the (mutated) live template."""
    data = Path(tex_path).read_bytes()
    return b"%PDF-" + hashlib.sha256(data).hexdigest().encode()


@pytest.fixture
def content_pdf(monkeypatch):
    monkeypatch.setattr(docs, "_compile", _content_compile)


def _make_paid_reg(app, *, amount=11000, txn="TXN-R"):
    from app.models import User, Conference, Registration
    tag = secrets.token_hex(4)
    with app.app_context():
        u = User(email=f"payer-{tag}@example.org", full_name="Payer One",
                 role_name="member")
        db.session.add(u)
        db.session.flush()
        c = Conference(slug=f"conf-{tag}", title="Physics 2026",
                       start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
        db.session.add(c)
        db.session.flush()
        reg = Registration(user_id=u.id, conference_id=c.id, tier_name="Standard",
                           amount=amount, status="paid", transaction_id=txn)
        db.session.add(reg)
        db.session.commit()
        return reg.id, u.email


# --- (1) issue records a faithful snapshot on the auto + manual paths ---------

def test_auto_receipt_records_issued_document(seed_templates, app, mailbox,
                                              content_pdf):
    reg_id, email = _make_paid_reg(app)
    with app.app_context():
        from app.models import (get_document_template, get_financial_identity,
                                IssuedDocument, Registration)
        from app.services.invoice import send_invoice_email

        tpl = get_document_template("receipt")
        tpl.pdf_body = "Receipt body {conference_title}"
        ident = get_financial_identity()
        ident.gst_registered = True
        ident.abn = "11 222 333 444"
        db.session.commit()

        reg = db.session.get(Registration, reg_id)
        assert send_invoice_email(reg) is True

        rows = IssuedDocument.query.filter_by(kind="receipt", recipient=email).all()
        assert len(rows) == 1
        row = rows[0]
        # vars snapshot carries ALL resolved variables actually rendered.
        v = json.loads(row.vars_json)
        assert v["user_email"] == email
        assert v["conference_title"] == "Physics 2026"
        assert v["transaction_id"] == "TXN-R"
        # Issuer/tax values ride in the variable snapshot (resolved from the
        # financial identity at send time), so the template snapshot is just
        # the body.
        assert v["business_number"] == "11 222 333 444"
        assert v["gst_applies"] == "1"
        t = json.loads(row.template_json)
        assert t == {"pdf_body": "Receipt body {conference_title}"}
        assert row.content_hash == tpl.content_hash
        assert row.amount == 11000


def test_manual_invoice_records_issued_document(seed_templates, app, mailbox,
                                                content_pdf):
    ref = f"INV-{secrets.token_hex(3)}"
    with app.app_context():
        from app.models import IssuedDocument
        from app.services.invoice import send_manual_invoice
        send_manual_invoice("sponsor@example.org", recipient_name="Sponsor Co",
                            description="Gold sponsorship", item="Sponsorship",
                            amount_cents=50000, reference=ref)
        rows = IssuedDocument.query.filter_by(reference=ref).all()
        assert len(rows) == 1
        assert rows[0].kind == "invoice"
        assert rows[0].recipient == "sponsor@example.org"
        v = json.loads(rows[0].vars_json)
        assert v["transaction_id"] == ref
        # The durable pay link is part of the rendered vars → captured for regen.
        assert f"/pay/invoice/{ref}" in v["payment_link"]


# --- (2) NO row for a test invoice or a preview ------------------------------

def test_test_invoice_records_no_issued_document(seed_templates, app, mailbox,
                                                 content_pdf):
    with app.app_context():
        from app.models import IssuedDocument
        from app.services.invoice import send_test_invoice
        before = IssuedDocument.query.count()
        send_test_invoice("proof@example.org")
        assert IssuedDocument.query.count() == before


def test_no_send_path_leaks_an_unresolved_placeholder(seed_templates, app,
                                                      mailbox, content_pdf):
    """Every {placeholder} the shipped covers use must be resolved by every
    path that sends them. `_render` leaves unknown names verbatim, so a
    variable a path forgets to supply is emailed as literal `{payment_link}` —
    which is exactly what the test invoice was doing, since the invoice cover
    asks the payer to "Pay online: {payment_link}".
    """
    import re

    with app.app_context():
        from app.services.invoice import send_manual_invoice, send_test_invoice

        send_test_invoice("proof@example.org")
        send_manual_invoice(
            "sponsor@example.org", recipient_name="Sponsor Pty Ltd",
            description="Gold sponsorship", item="Gold tier",
            amount_cents=110000, reference=f"INV-{secrets.token_hex(3)}")

    assert mailbox, "nothing was sent"
    for msg in mailbox:
        body = msg.get("body") or msg.get("text") or ""
        leaked = re.findall(r"\{[a-z_]+\}", body)
        assert not leaked, f"unresolved placeholders in {msg.get('subject')!r}: {leaked}"


def test_preview_records_no_issued_document(seed_templates, app, monkeypatch):
    with app.app_context():
        from app.models import IssuedDocument
        monkeypatch.setattr(docs, "render_document", lambda *a, **k: b"%PDF")
        before = IssuedDocument.query.count()
        docs.preview_document("invoice", overrides={"amount": "1.00"})
        assert IssuedDocument.query.count() == before


# --- (3) regeneration is deterministic + faithful after a template mutation ---

def test_regenerate_is_deterministic_and_faithful(seed_templates, app, mailbox,
                                                  content_pdf):
    ref = f"INV-{secrets.token_hex(3)}"
    with app.app_context():
        from app.models import get_document_template, IssuedDocument
        from app.services.invoice import send_manual_invoice

        tpl = get_document_template("invoice")
        tpl.pdf_body = "Thanks for {conference_title}"
        db.session.commit()

        send_manual_invoice("sponsor@example.org", recipient_name="Sponsor Co",
                            description="Gold sponsorship", item="Sponsorship",
                            amount_cents=50000, reference=ref)
        issued = IssuedDocument.query.filter_by(reference=ref).first()
        assert issued is not None

        before = docs.regenerate_document(issued)
        assert before[:4] == b"%PDF"

        # Mutate the LIVE template — regeneration must ignore it (snapshot is
        # the source of truth), so the bytes are identical across the change.
        tpl.pdf_body = "COMPLETELY DIFFERENT {conference_title}"
        db.session.commit()
        after = docs.regenerate_document(issued)
        assert after == before

        # A live render with the mutated template WOULD differ — this proves the
        # equality above is faithfulness to the snapshot, not a no-op stub.
        live = docs.render_document("invoice", json.loads(issued.vars_json))
        assert live != before


# --- (4) single download route streams a regenerated PDF ---------------------

def test_download_route_streams_pdf(seed_templates, admin_client, app, monkeypatch):
    ref = f"DL-{secrets.token_hex(3)}"
    monkeypatch.setattr(docs, "_compile", lambda *a, **k: b"%PDF-dl")
    with app.app_context():
        from app.models import IssuedDocument
        row = IssuedDocument(kind="receipt", reference=ref,
                             recipient="a@example.org", amount=5000,
                             vars_json="{}",
                             template_json='{"pdf_body":"","gst_registered":false}',
                             content_hash="x")
        db.session.add(row)
        db.session.commit()
        doc_id = row.id

    resp = admin_client.post(
        f"/admin/financial/document/issued/{doc_id}/download")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    disp = resp.headers.get("Content-Disposition", "")
    assert "attachment" in disp and f"receipt-{ref}.pdf" in disp
    assert resp.data == b"%PDF-dl"


def test_download_route_missing_document_flashes(seed_templates, admin_client):
    resp = admin_client.post("/admin/financial/document/issued/999999/download",
                             follow_redirects=True)
    assert resp.status_code == 200
    assert b"could not be found" in resp.data


# --- (5) bulk zip regenerates matching docs and respects the cap -------------

def _seed_issued(app, ref, kind="invoice"):
    """A ledger event + a stored document under `ref` so the filter picks it up."""
    from app.models import IssuedDocument, record_payment_event
    with app.app_context():
        record_payment_event(merchant_reference=ref, event_type="invoice.sent",
                             amount=5000, note="seeded")
        db.session.add(IssuedDocument(
            kind=kind, reference=ref, recipient="s@example.org", amount=5000,
            vars_json="{}",
            template_json='{"pdf_body":"","gst_registered":false}',
            content_hash="x"))
        db.session.commit()


def test_bulk_zip_contains_regenerated_files(seed_templates, admin_client, app,
                                             monkeypatch):
    tag = secrets.token_hex(4)
    monkeypatch.setattr(docs, "_compile", lambda *a, **k: b"%PDF-zip")
    for i in range(3):
        _seed_issued(app, f"ZIP-{tag}-{i}")

    resp = admin_client.post(
        f"/admin/financial/documents/download-zip?q=ZIP-{tag}", data={})
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    zf = zipfile.ZipFile(BytesIO(resp.data))
    names = zf.namelist()
    assert len(names) == 3
    assert all(n.endswith(".pdf") for n in names)
    assert zf.read(names[0]) == b"%PDF-zip"


def test_bulk_zip_respects_cap(seed_templates, admin_client, app, monkeypatch):
    tag = secrets.token_hex(4)
    monkeypatch.setattr(docs, "_compile", lambda *a, **k: b"%PDF-zip")
    monkeypatch.setattr("app.blueprints.admin.financial._BULK_DOC_CAP", 2)
    for i in range(3):
        _seed_issued(app, f"CAP-{tag}-{i}")

    resp = admin_client.post(
        f"/admin/financial/documents/download-zip?q=CAP-{tag}",
        data={}, follow_redirects=True)
    assert resp.status_code == 200
    # Capped download streams the zip directly; a follow-redirect on a 200 file
    # response just returns the file. Assert the zip holds exactly the cap.
    zf = zipfile.ZipFile(BytesIO(resp.data))
    assert len(zf.namelist()) == 2


def test_bulk_zip_no_matches_flashes(seed_templates, admin_client):
    resp = admin_client.post(
        "/admin/financial/documents/download-zip?q=NOTHING-MATCHES-XYZ",
        data={}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"No stored documents match" in resp.data


# --- (6) migration guard pattern (mechanical) --------------------------------

def test_migration_follows_house_guard_rules():
    """The new-table migration must chain off the document_template head, guard
    create_table/index behind an sa.inspect existence check (db.create_all may
    pre-create the table), and drop the table on downgrade."""
    path = (Path(__file__).resolve().parent.parent / "migrations" / "versions"
            / "d1f4a2c9b8e6_add_issued_documents.py")
    src = path.read_text()
    assert "down_revision = 'b3f7a9c1e2d4'" in src
    assert "sa.inspect(bind).get_table_names()" in src
    assert "if 'issued_documents' not in existing:" in src
    assert "op.create_table('issued_documents'" in src
    assert "op.drop_table('issued_documents')" in src


# --- member-facing invoice/receipt downloads ---------------------------------

@pytest.fixture
def compiles(monkeypatch):
    """Record the .tex of every real compile; identical input, identical bytes."""
    seen: list[str] = []

    def _counting(tectonic, job_dir, tex_path, epoch, memory_mb=0,
                  should_abort=None, timeout=0):
        seen.append(Path(tex_path).read_text(encoding="utf-8"))
        return _content_compile(tectonic, job_dir, tex_path, epoch)

    monkeypatch.setattr(docs, "_compile", _counting)
    return seen


def _unpaid_reg(app, *, amount=50000):
    from app.models import User, Conference, Registration
    tag = secrets.token_hex(4)
    with app.app_context():
        u = User(email=f"owes-{tag}@example.org", full_name="Owes Money",
                 role_name="member")
        db.session.add(u)
        db.session.flush()
        c = Conference(slug=f"conf-{tag}", title="Physics 2026",
                       start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
        db.session.add(c)
        db.session.flush()
        reg = Registration(user_id=u.id, conference_id=c.id,
                           tier_name="Standard", amount=amount, status="pending")
        db.session.add(reg)
        db.session.commit()
        reg.charge_to(amount, reason="Standard")
        return reg.id


class TestDocumentCaching:
    """A document's bytes are a pure function of its inputs, so it need only
    ever be compiled once."""

    def _fetch(self, app, rid, kind="invoice"):
        from app.models import Registration
        from app.services.invoice import registration_document
        with app.app_context():
            return registration_document(Registration.query.get(rid), kind)

    def test_a_second_request_does_not_compile_again(self, seed_templates,
                                                     compiles):
        app = seed_templates
        rid = _unpaid_reg(app)

        first = self._fetch(app, rid)
        second = self._fetch(app, rid)

        assert first == second
        assert len(compiles) == 1

    def test_a_changed_balance_rebuilds_once_then_caches(self, seed_templates,
                                                         compiles):
        from app.models import Registration
        app = seed_templates
        rid = _unpaid_reg(app, amount=50000)
        self._fetch(app, rid)

        with app.app_context():
            reg = Registration.query.get(rid)
            reg.charge_to(65000, reason="upgraded")

        self._fetch(app, rid)
        self._fetch(app, rid)
        assert len(compiles) == 2

    def test_the_invoice_asks_for_the_balance_not_the_tier_price(
            self, seed_templates, compiles):
        """Part paid: the invoice must ask for what is left."""
        from app.models import Registration, record_payment_event
        app = seed_templates
        rid = _unpaid_reg(app, amount=50000)
        with app.app_context():
            record_payment_event(transaction_id="PAY-PART",
                                 merchant_reference=f"reg_{rid}",
                                 registration_id=rid,
                                 event_type="payment.captured", amount=20000)
            assert Registration.query.get(rid).amount_due == 30000

        self._fetch(app, rid)
        assert "300.00" in compiles[0]
        assert "500.00" not in compiles[0]

    def test_a_receipt_matches_the_document_that_was_emailed(
            self, seed_templates, content_pdf, mailbox):
        from app.models import Registration
        from app.services.invoice import registration_document, send_invoice_email
        app = seed_templates
        rid, _ = _make_paid_reg(app)

        with app.app_context():
            send_invoice_email(Registration.query.get(rid))
        emailed = mailbox[-1]["attachments"][0][1]

        with app.app_context():
            assert registration_document(
                Registration.query.get(rid), "receipt") == emailed

    def test_a_receipt_falls_back_when_none_was_recorded(self, seed_templates,
                                                         compiles):
        """The row is absent when the render or the send failed at capture."""
        from app.models import IssuedDocument, Registration, record_payment_event
        app = seed_templates
        rid, _ = _make_paid_reg(app, amount=11000)
        with app.app_context():
            record_payment_event(transaction_id="PAY-CAP",
                                 merchant_reference=f"reg_{rid}",
                                 registration_id=rid,
                                 event_type="payment.captured", amount=11000)
            from app.services.invoice import (_reg_merchant_reference,
                                              registration_document)
            reg = Registration.query.get(rid)
            assert IssuedDocument.query.filter_by(
                reference=_reg_merchant_reference(reg)).count() == 0
            pdf = registration_document(reg, "receipt")

        assert pdf.startswith(b"%PDF-")
        assert "110.00" in compiles[0]


class TestMemberDocumentRoute:
    def _login_owner(self, app, rid, client):
        from app.models import Registration
        with app.app_context():
            uid = Registration.query.get(rid).user_id
        with client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True

    def test_the_owner_can_download_an_invoice(self, seed_templates, client,
                                               content_pdf):
        app = seed_templates
        rid = _unpaid_reg(app)
        self._login_owner(app, rid, client)

        resp = client.get(f"/registrations/{rid}/document/invoice")
        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"

    def test_another_member_cannot(self, seed_templates, member_client,
                                   content_pdf):
        app = seed_templates
        rid = _unpaid_reg(app)
        assert member_client.get(
            f"/registrations/{rid}/document/invoice").status_code == 403

    def test_a_receipt_is_refused_while_unpaid(self, seed_templates, client,
                                               content_pdf):
        app = seed_templates
        rid = _unpaid_reg(app)
        self._login_owner(app, rid, client)

        resp = client.get(f"/registrations/{rid}/document/receipt",
                          follow_redirects=True)
        assert b"once your payment has gone through" in resp.data

    def test_an_invoice_is_refused_once_nothing_is_owed(self, seed_templates,
                                                        client, content_pdf):
        app = seed_templates
        rid, _ = _make_paid_reg(app)
        self._login_owner(app, rid, client)

        resp = client.get(f"/registrations/{rid}/document/invoice",
                          follow_redirects=True)
        assert b"Nothing is outstanding" in resp.data

    def test_an_unknown_kind_is_not_served(self, seed_templates, client):
        app = seed_templates
        rid = _unpaid_reg(app)
        self._login_owner(app, rid, client)
        assert client.get(
            f"/registrations/{rid}/document/statement").status_code == 404
