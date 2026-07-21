"""Send-layer tests (plan build step 6): PDFs attached to the real send paths,
the §7 failure handling, the durable pay link, and the payment-success page.

Every compile is stubbed at the pure `_compile` step (or `render_document` is
replaced) so no real tectonic runs here — the send/failure/link behaviour is
what's under test, not the LaTeX toolchain.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.extensions import db
from app.services import documents as docs
from app.services.documents import RenderError
from app.services.gateways import CheckoutResult, WebhookResult


# --- fixtures ----------------------------------------------------------------

@pytest.fixture
def seed_templates(app):
    """Seed the three document templates so the send layer can resolve covers."""
    with app.app_context():
        from app.models import get_document_template
        for kind in ("invoice", "receipt", "adjustment"):
            get_document_template(kind)
    return app


@pytest.fixture
def mailbox(monkeypatch):
    """Capture every send_mail call across the send layer and the payment-
    attention channel (which re-imports send_mail locally)."""
    box: list[dict] = []

    def _record(**kw):
        box.append(kw)
        return True

    monkeypatch.setattr("app.services.invoice.send_mail", _record)
    monkeypatch.setattr("app.services.mail.send_mail", _record)
    return box


@pytest.fixture
def fake_pdf(monkeypatch):
    """Stub the pure compile step so render_document yields fake PDF bytes."""
    monkeypatch.setattr(docs, "_compile", lambda *a, **k: b"%PDF-fake")


def _make_reg(app, *, status="pending", amount=11000, txn="TXN-1"):
    """Create a user + conference + registration (unique per call — the test DB
    is session-scoped) plus a checkout.created ledger event, as the real flow
    does. Returns (reg_id, merchant_reference, payer_email)."""
    import secrets
    from app.models import User, Conference, Registration, record_payment_event
    tag = secrets.token_hex(4)
    email = f"payer-{tag}@example.org"
    with app.app_context():
        u = User(email=email, full_name="Payer One", role_name="member")
        db.session.add(u)
        db.session.flush()
        c = Conference(slug=f"conf-{tag}", title="Physics 2026",
                       start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
        db.session.add(c)
        db.session.flush()
        reg = Registration(user_id=u.id, conference_id=c.id, tier_name="Standard",
                           amount=amount, status=status, transaction_id=txn)
        db.session.add(reg)
        db.session.commit()
        ref = f"reg_{reg.id}-{tag}"
        record_payment_event(transaction_id="hc-1", merchant_reference=ref,
                             registration_id=reg.id, event_type="checkout.created",
                             amount=amount, note="hosted checkout session created")
        return reg.id, ref, email


def _post_webhook(client, monkeypatch, result: WebhookResult):
    """POST a webhook whose verification yields `result` (gateway stubbed)."""
    class _Gateway:
        def verify_webhook(self, body, headers):
            return result
    monkeypatch.setattr("app.services.payments._active_gateway",
                        lambda: _Gateway())
    return client.post("/payments/webhook", data=b"{}",
                       content_type="application/json")


# --- (1) auto-send attaches a PDF with the right filename/mimetype -----------

def test_auto_receipt_attaches_pdf(seed_templates, client, monkeypatch,
                                   mailbox, fake_pdf):
    app = seed_templates
    reg_id, ref, payer_email = _make_reg(app)
    result = WebhookResult(success=True, registration_id=reg_id,
                           transaction_id="TXN-1", event_type="payment.captured",
                           merchant_reference=ref, amount=11000)
    resp = _post_webhook(client, monkeypatch, result)
    assert resp.status_code == 200

    # Receipt email to the payer carries the PDF with the right name + type.
    to_payer = [m for m in mailbox if m["to"] == payer_email]
    assert to_payer, mailbox
    att = to_payer[0]["attachments"]
    assert att == [("receipt-TXN-1.pdf", b"%PDF-fake", "application/pdf")]

    with app.app_context():
        from app.models import PaymentEvent
        assert PaymentEvent.query.filter_by(merchant_reference=ref,
                                            event_type="document.sent").count() == 1


# --- (2) forced RenderError on capture → §7 handling -------------------------

def test_capture_render_failure_is_handled(seed_templates, client, monkeypatch,
                                           mailbox):
    app = seed_templates
    reg_id, ref, payer_email = _make_reg(app)

    def _boom(*a, **k):
        raise RenderError("tectonic exited 1", log="! Undefined control sequence")
    monkeypatch.setattr(docs, "_compile", _boom)

    result = WebhookResult(success=True, registration_id=reg_id,
                           transaction_id="TXN-1", event_type="payment.captured",
                           merchant_reference=ref, amount=11000)
    resp = _post_webhook(client, monkeypatch, result)
    # The webhook never 500s because of a render failure.
    assert resp.status_code == 200

    # Degraded plaintext email confirming the payment, NO attachment.
    payer = [m for m in mailbox if m["to"] == payer_email]
    assert payer, mailbox
    assert not payer[0].get("attachments")
    assert "processed successfully" in payer[0]["body"]

    # Admin alert through the payment-attention channel, carrying the log.
    admin = [m for m in mailbox if "attention" in m["subject"].lower()]
    assert admin, mailbox
    assert "Undefined control sequence" in admin[0]["body"]

    # document.pending recorded in the same ledger group as the payment.
    with app.app_context():
        from app.models import PaymentEvent
        pend = PaymentEvent.query.filter_by(merchant_reference=ref,
                                            event_type="document.pending").all()
        assert len(pend) == 1
        assert pend[0].registration_id == reg_id


# --- (3) retry route re-sends and records document.sent ----------------------

def test_retry_route_resends_and_records(seed_templates, admin_client, app,
                                         monkeypatch, mailbox):
    reg_id, ref, payer_email = _make_reg(app)
    with app.app_context():
        from app.models import record_payment_event
        # Mark the reg paid and leave a pending document to retry.
        from app.models import Registration
        reg = db.session.get(Registration, reg_id)
        reg.status = "paid"
        db.session.commit()
        record_payment_event(transaction_id="TXN-1", merchant_reference=ref,
                             registration_id=reg_id, event_type="document.pending",
                             amount=11000, note="receipt: broke")

    monkeypatch.setattr(docs, "_compile", lambda *a, **k: b"%PDF-fake")
    resp = admin_client.post(f"/admin/financial/document/{ref}/send-pending",
                             follow_redirects=True)
    assert resp.status_code == 200

    payer = [m for m in mailbox if m["to"] == payer_email]
    assert payer and payer[0]["attachments"][0][0] == "receipt-TXN-1.pdf"
    with app.app_context():
        from app.models import PaymentEvent
        assert PaymentEvent.query.filter_by(merchant_reference=ref,
                                            event_type="document.sent").count() == 1


def test_retry_route_rejects_when_not_pending(seed_templates, admin_client, app):
    reg_id, ref, _ = _make_reg(app)   # only a checkout.created, no pending doc
    resp = admin_client.post(f"/admin/financial/document/{ref}/send-pending",
                             follow_redirects=True)
    assert resp.status_code == 200
    assert b"No pending document" in resp.data


# --- (4) manual invoice RenderError → flash + nothing recorded ---------------

def test_manual_invoice_render_error_records_nothing(seed_templates, admin_client,
                                                     app, monkeypatch):
    monkeypatch.setattr("app.services.invoice.render_document",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RenderError("boom", log="! bad")))
    with app.app_context():
        from app.models import PaymentEvent
        before = PaymentEvent.query.count()

    resp = admin_client.post("/admin/financial/send-invoice", data={
        "to": "sponsor@example.org", "description": "Gold sponsorship",
        "item": "Sponsorship", "amount": "500.00", "reference": "INV-FAIL",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"could not be generated" in resp.data
    with app.app_context():
        from app.models import PaymentEvent
        assert PaymentEvent.query.count() == before
        assert PaymentEvent.query.filter_by(merchant_reference="INV-FAIL").count() == 0


# --- (7) {payment_link} lands in the manual invoice body and PDF vars --------

def test_manual_invoice_embeds_pay_link(seed_templates, app, mailbox, monkeypatch):
    captured = {}

    def _fake_render(kind, vars_, *a, **k):
        captured.update(vars_)
        return b"%PDF-fake"
    monkeypatch.setattr("app.services.invoice.render_document", _fake_render)

    with app.app_context():
        from app.models import get_document_template
        from app.services.invoice import (default_manual_invoice_body,
                                           send_manual_invoice)
        body = default_manual_invoice_body(get_document_template("invoice"))
        # The default manual-invoice body template carries the {payment_link} var.
        assert "{payment_link}" in body
        send_manual_invoice("sponsor@example.org", recipient_name="Sponsor Co",
                            description="Gold sponsorship", item="Sponsorship",
                            amount_cents=50000, reference="INV-LINK",
                            body_override=body)

    link = "/pay/invoice/INV-LINK"
    # PDF vars carry the resolved durable link…
    assert link in captured["payment_link"]
    # …and the emailed cover body (the default manual body) resolves it too.
    sent = [m for m in mailbox if m["to"] == "sponsor@example.org"]
    assert sent and link in sent[0]["body"]
    assert sent[0]["attachments"][0][0] == "invoice-INV-LINK.pdf"


# --- (5) durable pay link: mint checkout + redirect, reject unknown/paid -----

def _seed_invoice(app, reference="INV-PAY", amount=50000,
                  note="to sponsor@example.org by admin@test.example.org"):
    from app.models import record_payment_event
    with app.app_context():
        record_payment_event(merchant_reference=reference, event_type="invoice.sent",
                             amount=amount, note=note)


def test_pay_link_mints_checkout_and_redirects(seed_templates, client, app,
                                               monkeypatch):
    _seed_invoice(app)

    class _Gateway:
        def create_invoice_checkout(self, amount, reference, return_url, currency="AUD"):
            assert reference == "INV-PAY" and amount == 50000
            return CheckoutResult(redirect_url="https://worldline.example/hc",
                                  payment_id="hc-9", merchant_reference=reference)
    monkeypatch.setattr("app.services.payments._active_gateway", lambda: _Gateway())

    resp = client.get("/pay/invoice/INV-PAY")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://worldline.example/hc"
    with app.app_context():
        from app.models import PaymentEvent
        assert PaymentEvent.query.filter_by(merchant_reference="INV-PAY",
                                            event_type="checkout.created").count() == 1


def test_pay_link_unknown_reference_rejected(seed_templates, client):
    resp = client.get("/pay/invoice/does-not-exist")
    assert resp.status_code == 200
    assert b"not valid or is no longer available" in resp.data


def test_pay_link_paid_reference_rejected(seed_templates, client, app, monkeypatch):
    _seed_invoice(app, reference="INV-PAID")
    from app.models import record_payment_event
    with app.app_context():
        record_payment_event(merchant_reference="INV-PAID",
                             event_type="payment.captured", amount=50000)
    # Even with a working gateway, a paid invoice must not mint a new checkout.
    monkeypatch.setattr("app.services.payments._active_gateway", lambda: object())
    resp = client.get("/pay/invoice/INV-PAID")
    assert resp.status_code == 200
    assert b"not valid or is no longer available" in resp.data


def test_pay_link_gateway_offline_shows_eft(seed_templates, client, app, monkeypatch):
    _seed_invoice(app, reference="INV-OFF")
    monkeypatch.setattr("app.services.payments._active_gateway", lambda: None)
    resp = client.get("/pay/invoice/INV-OFF")
    assert resp.status_code == 200
    assert b"Online card payment is currently unavailable" in resp.data
    assert b"INV-OFF" in resp.data


# --- (6) result page reflects ledger state -----------------------------------

def test_result_page_states(seed_templates, client, app):
    from app.models import record_payment_event
    _seed_invoice(app, reference="INV-RES")
    # open (invoice.sent only)
    r = client.get("/pay/invoice/INV-RES/result")
    assert r.status_code == 200 and b"Payment Pending" in r.data
    with app.app_context():
        record_payment_event(merchant_reference="INV-RES",
                             event_type="checkout.created", amount=50000)
    assert b"Payment Processing" in client.get("/pay/invoice/INV-RES/result").data
    with app.app_context():
        record_payment_event(merchant_reference="INV-RES",
                             event_type="payment.captured", amount=50000)
    assert b"Payment Successful" in client.get("/pay/invoice/INV-RES/result").data


# --- (8) capture on an INV reference sends the receipt to the stored recipient

def test_capture_on_invoice_sends_receipt(seed_templates, client, app,
                                          monkeypatch, mailbox, fake_pdf):
    _seed_invoice(app, reference="INV-CAP")
    result = WebhookResult(success=True, registration_id=None,
                           transaction_id="TXN-CAP", event_type="payment.captured",
                           merchant_reference="INV-CAP", amount=50000)
    resp = _post_webhook(client, monkeypatch, result)
    assert resp.status_code == 200

    sponsor = [m for m in mailbox if m["to"] == "sponsor@example.org"]
    assert sponsor, mailbox
    assert sponsor[0]["attachments"][0][0] == "receipt-INV-CAP.pdf"
    with app.app_context():
        from app.models import PaymentEvent
        assert PaymentEvent.query.filter_by(merchant_reference="INV-CAP",
                                            event_type="document.sent").count() == 1
