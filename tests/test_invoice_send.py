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
    # payments.py binds send_mail at import, so patching app.services.mail
    # alone leaves the registration confirmation and payment request escaping.
    monkeypatch.setattr("app.services.payments.send_mail", _record)
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


def _sponsorship(app, price=50000, name="Gold"):
    """A conference with one priced sponsorship level — the pair the Send
    Invoice form now asks for instead of free-text description/item/amount.
    Returns (conference_id, tier_id)."""
    import secrets
    from app.models import Conference
    from app.models.sponsor import SponsorTier
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"sponsor-conf-{tag}", title="Physics 2026",
                       start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
        db.session.add(c)
        db.session.flush()
        t = SponsorTier(conference_id=c.id, name=name, display_order=10,
                        price=price)
        db.session.add(t)
        db.session.commit()
        return c.id, t.id


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
    conf_id, tier_id = _sponsorship(app)
    with app.app_context():
        from app.models import PaymentEvent
        before = PaymentEvent.query.count()

    resp = admin_client.post("/admin/financial/send-invoice", data={
        "to": "sponsor@example.org", "conference_id": str(conf_id),
        "tier_id": str(tier_id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"could not be generated" in resp.data
    with app.app_context():
        from app.models import PaymentEvent
        # Nothing recorded at all — not even under the reference it minted.
        assert PaymentEvent.query.count() == before


# --- {sanitized_invoice_ref}: a bank-safe reference the payer can quote ------

def test_sanitized_reference_is_bank_safe_and_lossless():
    from app.services.invoice import sanitized_reference

    assert sanitized_reference("INV-20260806-28D4") == "INV2026080628D4"
    # Inside the 18-character BECS lodgement reference field.
    assert len(sanitized_reference("INV-20260806-28D4")) <= 18
    # Lossless: the letters stay, so two references cannot collide. A
    # digits-only form would map both of these onto 20260806284.
    assert (sanitized_reference("INV-20260806-28D4")
            != sanitized_reference("INV-20260806-284D"))
    assert sanitized_reference("") == ""


def test_payment_instructions_expand_the_sanitized_ref(seed_templates, app,
                                                       mailbox, monkeypatch):
    """An admin writes "REF: {sanitized_invoice_ref}" in Financial identity and
    it reaches the payer resolved — in the PDF variables and the email body.

    payment_instructions is a leaf value in a single-pass render, so without
    the expansion pass it would print the raw braces.
    """
    captured = {}

    def _fake_render(kind, vars_, *a, **k):
        captured.update(vars_)
        return b"%PDF-fake"
    monkeypatch.setattr("app.services.invoice.render_document", _fake_render)

    with app.app_context():
        from app.models import get_financial_identity
        from app.services.invoice import send_manual_invoice
        ident = get_financial_identity()
        ident.payment_instructions = ("BSB: 015142\nACCOUNT: 457357624\n"
                                      "REF: {sanitized_invoice_ref}")
        db.session.commit()

        send_manual_invoice("sponsor@example.org", recipient_name="Sponsor Co",
                            description="Gold sponsorship", item="Sponsorship",
                            amount_cents=50000, reference="INV-20260806-28D4",
                            body_override="Pay to:\n{payment_instructions}")

    assert captured["sanitized_invoice_ref"] == "INV2026080628D4"
    assert "REF: INV2026080628D4" in captured["payment_instructions"]
    assert "{sanitized_invoice_ref}" not in captured["payment_instructions"]

    sent = [m for m in mailbox if m["to"] == "sponsor@example.org"]
    assert sent and "REF: INV2026080628D4" in sent[0]["body"]


def test_ledger_search_matches_the_sanitized_reference(admin_client, app):
    """The treasurer pastes what the bank statement shows — the sanitized form
    — and must find the invoice stored under its punctuated reference."""
    _seed_invoice(app, reference="INV-20260806-28D4")

    hit = admin_client.get("/admin/financial/transactions?q=INV2026080628D4")
    assert b"INV-20260806-28D4" in hit.data

    # The punctuated form still works, and an unrelated reference does not.
    hit = admin_client.get("/admin/financial/transactions?q=INV-20260806-28D4")
    assert b"INV-20260806-28D4" in hit.data
    miss = admin_client.get("/admin/financial/transactions?q=INV9999999999")
    assert b"INV-20260806-28D4" not in miss.data


# --- standing CC: remembered per sender, never site-wide ---------------------

def test_invoice_cc_default_is_remembered_per_user(seed_templates, admin_client,
                                                   app, mailbox, monkeypatch,
                                                   login_user_session):
    """Ticking "remember" stores the CC against the admin who sent it.

    Per-user, not site-wide: two treasurers copy different people, and a shared
    admin machine must not hand one person's standing CC to the next login.
    """
    monkeypatch.setattr("app.services.invoice.render_document",
                        lambda *a, **k: b"%PDF-fake")
    conf_id, tier_id = _sponsorship(app)

    admin_client.post("/admin/financial/send-invoice", data={
        "to": "sponsor@example.org", "conference_id": str(conf_id),
        "tier_id": str(tier_id), "cc": "treasurer@example.org",
        "cc_default": "1",
    }, follow_redirects=True)

    with app.app_context():
        from app.models import User
        u = User.query.filter_by(email="admin@test.example.org").first()
        assert u.invoice_cc_default == "treasurer@example.org"

    # Comes back prefilled on this admin's next blank form…
    page = admin_client.get("/admin/financial/send-invoice")
    assert b'value="treasurer@example.org"' in page.data

    # …and lands on that admin's row alone, so a second treasurer starts clean.
    # (Asserted on the model rather than through a second logged-in request:
    # pytest-flask pushes one request context per test, so `current_user` is
    # cached and cannot be swapped mid-test.)
    other = login_user_session(email="other-admin@test.example.org",
                               full_name="Other Admin", role_name="admin")
    with app.app_context():
        from app.models import User
        assert not db.session.get(User, other).invoice_cc_default


def test_invoice_cc_default_untouched_when_box_unticked(seed_templates,
                                                        admin_client, app,
                                                        mailbox, monkeypatch):
    """A one-off CC for a single sponsor must not overwrite the standing one."""
    monkeypatch.setattr("app.services.invoice.render_document",
                        lambda *a, **k: b"%PDF-fake")
    conf_id, tier_id = _sponsorship(app)

    with app.app_context():
        from app.models import User
        u = User.query.filter_by(email="admin@test.example.org").first()
        u.invoice_cc_default = "treasurer@example.org"
        db.session.commit()

    admin_client.post("/admin/financial/send-invoice", data={
        "to": "sponsor@example.org", "conference_id": str(conf_id),
        "tier_id": str(tier_id), "cc": "one-off@example.org",
    }, follow_redirects=True)

    with app.app_context():
        from app.models import User
        u = User.query.filter_by(email="admin@test.example.org").first()
        assert u.invoice_cc_default == "treasurer@example.org"


# --- (7) {payment_link} lands in the manual invoice body and PDF vars --------

def test_manual_invoice_embeds_pay_link(seed_templates, app, mailbox, monkeypatch):
    captured = {}

    def _fake_render(kind, vars_, *a, **k):
        captured.update(vars_)
        return b"%PDF-fake"
    monkeypatch.setattr("app.services.invoice.render_document", _fake_render)

    with app.app_context():
        from app.services.invoice import (default_manual_invoice_body,
                                           send_manual_invoice)
        body = default_manual_invoice_body()
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


class TestZeroFeeRegistration:
    """Sponsors, plenary speakers and comped attendees register on a zero
    fee tier. Billing them for $0.00 is nonsense, and leaving the
    registration pending parks it in the treasurer's unpaid list forever."""

    @staticmethod
    def _conference(app, amounts):
        """A conference accepting registrations, with a tier per amount.

        Returns (slug, [tier_name]) — the form selects a tier by NAME.
        """
        import secrets
        from datetime import date, timedelta
        from app.extensions import db
        from app.models import Conference
        from app.models.conference import PriceTier
        tag = secrets.token_hex(4)
        today = date.today()
        with app.app_context():
            c = Conference(slug=f"zero-{tag}", title="Physics 2026",
                           start_date=today + timedelta(days=30),
                           end_date=today + timedelta(days=32),
                           is_accepting_registrations=True, is_draft=False)
            db.session.add(c)
            db.session.flush()
            ids = []
            for i, amt in enumerate(amounts):
                t = PriceTier(conference_id=c.id, name=f"Tier{i}-{amt}",
                              amount=amt, display_order=i * 10)
                db.session.add(t)
                db.session.flush()
                ids.append(t.name)
            db.session.commit()
            return c.slug, ids

    def test_zero_fee_confirms_and_does_not_bill(self, seeded, app,
                                                 member_client, mailbox):
        from app.models import PaymentEvent, Registration
        slug, (free_tier,) = self._conference(app, [0])

        member_client.post(f"/conferences/{slug}/register",
                           data={"tier": free_tier},
                           follow_redirects=True)

        assert mailbox, "a zero-fee registration still gets a confirmation"
        sent = mailbox[-1]
        assert "confirmed" in sent["subject"].lower()
        assert "No payment is required" in sent["body"]
        # The ask is absent — no amount due, no pay link.
        assert "To complete your registration" not in sent["body"]

        with app.app_context():
            reg = Registration.query.order_by(Registration.id.desc()).first()
            assert reg.amount == 0
            # Settled, not parked in the unpaid list.
            assert reg.status == "paid"
            assert reg.payment_sent_at is None
            evts = PaymentEvent.query.filter_by(registration_id=reg.id).all()
            assert [e.event_type for e in evts] == ["registration.no_payment_due"]

    def test_paid_tier_still_bills(self, seeded, app, member_client, mailbox,
                                   monkeypatch):
        """The zero-fee branch must not swallow a real fee."""
        from app.models import Registration
        monkeypatch.setattr("app.blueprints.member.payments_open_to_members",
                            lambda: True)
        slug, (paid_tier,) = self._conference(app, [11000])

        member_client.post(f"/conferences/{slug}/register",
                           data={"tier": paid_tier},
                           follow_redirects=True)

        assert mailbox
        assert "To complete your registration" in mailbox[-1]["body"]
        with app.app_context():
            reg = Registration.query.order_by(Registration.id.desc()).first()
            assert reg.status == "pending"
            assert reg.payment_sent_at is not None

    def test_editing_a_paid_registration_does_not_rebill(self, seeded, app,
                                                         member_client, mailbox,
                                                         monkeypatch):
        """Changing a dietary note must not mark a paid attendee unpaid and
        send them a second payment request."""
        from app.extensions import db
        from app.models import Registration
        monkeypatch.setattr("app.blueprints.member.payments_open_to_members",
                            lambda: True)
        slug, (tier,) = self._conference(app, [11000])

        member_client.post(f"/conferences/{slug}/register",
                           data={"tier": tier}, follow_redirects=True)
        with app.app_context():
            reg = Registration.query.order_by(Registration.id.desc()).first()
            rid = reg.id
            reg.status = "paid"
            db.session.commit()

        mailbox.clear()
        member_client.post(f"/conferences/{slug}/register",
                           data={"tier": tier, "dietary": "Vegetarian"},
                           follow_redirects=True)

        with app.app_context():
            reg = Registration.query.get(rid)
            assert reg.status == "paid"
            assert reg.dietary == "Vegetarian"
        assert not mailbox, "a settled registration was billed again on edit"


class TestDurableRegistrationPayLink:
    """The person who registers is often not the person who pays.

    An academic forwards the payment email to a grant administrator or a
    finance office with no account here, so the link carries its own authority
    — a capability token, not a session.
    """

    @staticmethod
    def _reg(app, status="pending", amount=11000):
        import secrets
        from app.models import Conference, Registration, User
        tag = secrets.token_hex(4)
        with app.app_context():
            u = User(email=f"payer-{tag}@example.org", full_name="Pat Payer",
                     role_name="member")
            db.session.add(u)
            db.session.flush()
            c = Conference(slug=f"tok-{tag}", title="Physics 2026",
                           start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
            db.session.add(c)
            db.session.flush()
            r = Registration(user_id=u.id, conference_id=c.id,
                             tier_name="Standard", amount=amount, status=status)
            db.session.add(r)
            db.session.commit()
            return r.id, r.ensure_pay_token()

    def test_token_is_minted_lazily_and_is_unguessable(self, seeded, app):
        from app.models import Registration
        rid, token = self._reg(app)
        _rid2, other = self._reg(app)
        with app.app_context():
            reg = Registration.query.get(rid)
            # Stable once minted — the emailed link must not change under the
            # payer on a later read.
            assert reg.ensure_pay_token() == token
            # Independent of the id and of each other, so holding one token
            # (or a reference) tells you nothing about another registration's.
            assert token != other
            assert reg.reference not in token
            assert len(token) >= 40

    def test_link_pays_without_logging_in(self, seeded, app, client,
                                          monkeypatch):
        """The whole point: no session, and it still reaches the pay page."""
        monkeypatch.setattr("app.services.payments.payments_open_to_members",
                            lambda: True)
        _rid, token = self._reg(app)
        page = client.get(f"/pay/registration/{token}")
        assert page.status_code == 200
        assert b"Physics 2026" in page.data
        # And the checkout form targets the token, not the login-gated route.
        assert token.encode() in page.data

    def test_unknown_token_reveals_nothing(self, seeded, app, client):
        page = client.get("/pay/registration/not-a-real-token")
        assert page.status_code == 200
        assert b"not valid or is no longer available" in page.data

    def test_a_paid_registration_cannot_be_paid_again(self, seeded, app, client,
                                                      monkeypatch):
        """A forwarded link outlives the payment. Two people holding the same
        link must not each be able to start a fresh checkout."""
        called = []
        monkeypatch.setattr("app.services.payments.initiate_payment",
                            lambda *a, **k: called.append(1) or "https://gw/x")
        _rid, token = self._reg(app, status="paid")

        page = client.get(f"/pay/registration/{token}")
        assert b"Already paid" in page.data

        # The POST is re-checked too — the page may have been rendered before
        # somebody else paid.
        resp = client.post(f"/pay/registration/{token}/checkout")
        assert b"Already paid" in resp.data
        assert not called, "a checkout was minted against a paid registration"

    def test_refunded_and_cancelled_are_closed_too(self, seeded, app, client):
        _rid, refunded = self._reg(app, status="refunded")
        _rid2, cancelled = self._reg(app, status="cancelled")
        assert b"Already refunded" in client.get(
            f"/pay/registration/{refunded}").data
        assert b"Registration cancelled" in client.get(
            f"/pay/registration/{cancelled}").data

    def test_payment_email_carries_the_token_link(self, seeded, app, mailbox):
        from app.models import Registration
        rid, token = self._reg(app)
        with app.app_context():
            from app.services.payments import payment_url_for, send_payment_email
            reg = Registration.query.get(rid)
            send_payment_email(reg, payment_url_for(reg))
        assert mailbox
        body = mailbox[-1]["body"]
        assert f"/pay/registration/{token}" in body
        # The sequential id never appears as a payable URL.
        assert f"/pay/{rid}" not in body

    def test_member_route_redirects_to_the_token_link(self, seeded, app,
                                                      member_client):
        """Dashboard buttons and links already in inboxes keep working."""
        from app.models import Registration, User
        rid, token = self._reg(app)
        with app.app_context():
            reg = Registration.query.get(rid)
            me = User.query.filter_by(email="member@test.example.org").first()
            reg.user_id = me.id
            db.session.commit()
        resp = member_client.get(f"/pay/{rid}")
        assert resp.status_code in (301, 302)
        assert token in resp.headers["Location"]

    def test_another_members_registration_is_not_reachable_by_id(
            self, seeded, app, member_client):
        """The id route still checks ownership — the token is the only way in
        without a session, and it is not derivable from the id."""
        rid, _token = self._reg(app)
        assert member_client.get(f"/pay/{rid}").status_code == 403
