"""Billing a conference's sub-events, tentative ones included.

A tentative sub-event records interest without charging. Starting to bill it
charges everyone already attending — returning anyone who had paid
everything else to pending — and emails each an invoice. From then on it is
charged when someone opts in. Mail is captured and compiles are stubbed: no
test here reaches a real address.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Registration


@pytest.fixture
def mailbox(monkeypatch):
    box: list[dict] = []
    for target in ("app.services.mail.send_mail", "app.services.payments.send_mail",
                   "app.services.invoice.send_mail",
                   "app.blueprints.admin.financial.send_mail"):
        monkeypatch.setattr(target, lambda **kw: box.append(kw) or True)
    return box


@pytest.fixture
def conf(app):
    """A conference taking registrations, with a $400 tier and a $50 workshop
    the registration form asks about. Tentative by default."""
    from app.models import Conference, SubEvent
    from app.models.conference import PriceTier

    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"se-{tag}", title="Physics 2026",
                       start_date=date.today() + timedelta(days=30),
                       end_date=date.today() + timedelta(days=32),
                       is_accepting_registrations=True, is_draft=False)
        db.session.add(c)
        db.session.flush()
        db.session.add(PriceTier(conference_id=c.id, name="Standard",
                                 amount=40000, display_order=0))
        se = SubEvent(conference_id=c.id, name="EMCR Workshop", price=5000,
                      tentative=True)
        db.session.add(se)
        db.session.flush()
        c.registration_form_schema = {"sections": [{"key": "p", "label": "P", "fields": [
            {"key": "tier-select", "type": "registration-tier", "label": "Tier",
             "required": True},
            {"key": "ws", "type": "sub-event", "label": "Workshop",
             "sub_event_id": se.id}]}]}
        db.session.commit()
        return {"id": c.id, "slug": c.slug, "se_id": se.id}


def _member_id(app):
    from app.models import User
    with app.app_context():
        u = User.query.filter_by(email="member@test.example.org").first()
        return u.id


def _register(client, conf, attending: str):
    return client.post(f"/conferences/{conf['slug']}/register",
                       data={"tier": "Standard",
                             "_sub_event_emcr_workshop_attending": attending},
                       follow_redirects=True)


def _reg(app, conf, *, email, status, attending=True, paid=0):
    """A registration made directly, with its charge line and any payment."""
    from app.models import User, record_payment_event
    with app.app_context():
        u = User(email=email, full_name=email.split("@")[0], role_name="member")
        db.session.add(u)
        db.session.flush()
        reg = Registration(user_id=u.id, conference_id=conf["id"],
                           tier_name="Standard", amount=40000, status=status,
                           sub_events={"emcr_workshop": {"attending": attending}})
        db.session.add(reg)
        db.session.commit()
        reg.charge_to(40000, reason="Standard")
        if paid:
            record_payment_event(registration_id=reg.id, transaction_id=f"TXN-{reg.id}",
                                 merchant_reference=f"reg_{reg.id}",
                                 event_type="payment.captured", amount=paid)
            reg.transaction_id = f"TXN-{reg.id}"
            db.session.commit()
        return reg.id


def _set(app, se_id, **fields):
    from app.models import SubEvent
    with app.app_context():
        se = db.session.get(SubEvent, se_id)
        for k, v in fields.items():
            setattr(se, k, v)
        db.session.commit()


# --- the registration form --------------------------------------------------

class TestTheForm:
    def test_a_tentative_workshop_says_so_and_charges_nothing(
            self, seeded, member_client, app, conf, mailbox):
        page = member_client.get(f"/conferences/{conf['slug']}/register")
        assert b"Additional cost: $50.00" in page.data
        assert b"Tentative: this goes ahead only if enough people" in page.data

        _register(member_client, conf, "yes")
        with app.app_context():
            reg = Registration.query.filter_by(conference_id=conf["id"]).one()
            assert reg.sub_events["emcr_workshop"] == {"attending": True}
            assert reg.total_price == 40000 and reg.amount_due == 40000

    def test_no_is_recorded(self, seeded, member_client, app, conf, mailbox):
        _register(member_client, conf, "no")
        with app.app_context():
            reg = Registration.query.filter_by(conference_id=conf["id"]).one()
            assert reg.sub_events == {"emcr_workshop": {"attending": False}}

    def test_a_billed_workshop_is_charged_when_opted_into(
            self, seeded, member_client, app, conf, mailbox):
        _set(app, conf["se_id"], tentative=False)
        page = member_client.get(f"/conferences/{conf['slug']}/register")
        assert b"Tentative" not in page.data
        _register(member_client, conf, "yes")
        with app.app_context():
            reg = Registration.query.filter_by(conference_id=conf["id"]).one()
            assert reg.sub_events["emcr_workshop"]["charged"] == 5000
            assert reg.total_price == 45000 and reg.amount_due == 45000

    def test_a_later_price_change_does_not_reprice_someone_billed(
            self, seeded, member_client, app, conf, mailbox):
        _set(app, conf["se_id"], tentative=False)
        _register(member_client, conf, "yes")
        _set(app, conf["se_id"], price=9900)
        db.session.expire_all()
        _register(member_client, conf, "yes")
        with app.app_context():
            reg = Registration.query.filter_by(conference_id=conf["id"]).one()
            assert reg.amount_due == 45000

    def test_withdrawing_from_an_unpaid_workshop_removes_the_charge(
            self, seeded, member_client, app, conf, mailbox):
        _set(app, conf["se_id"], tentative=False)
        _register(member_client, conf, "yes")
        db.session.expire_all()
        _register(member_client, conf, "no")
        with app.app_context():
            reg = Registration.query.filter_by(conference_id=conf["id"]).one()
            assert reg.amount_due == 40000

    def test_a_paid_member_opting_in_owes_the_workshop_and_can_pay_it(
            self, seeded, member_client, app, conf, mailbox, monkeypatch):
        """`paid` means nothing owed: opting in afterwards returns them to
        pending, which is what lets the pay link take the balance."""
        from app.models import record_payment_event
        _set(app, conf["se_id"], tentative=False)
        _register(member_client, conf, "no")
        with app.app_context():
            reg = Registration.query.filter_by(conference_id=conf["id"]).one()
            record_payment_event(registration_id=reg.id, transaction_id="T-PAID",
                                 merchant_reference=f"reg_{reg.id}",
                                 event_type="payment.captured", amount=40000)
            reg.status = "paid"
            db.session.commit()
            token = reg.ensure_pay_token()
        monkeypatch.setattr("app.blueprints.member.payments_open_to_members",
                            lambda: True)
        db.session.expire_all()
        _register(member_client, conf, "yes")
        with app.app_context():
            reg = Registration.query.filter_by(conference_id=conf["id"]).one()
            assert reg.status == "pending" and reg.amount_due == 5000
        sent = [m for m in mailbox if m["to"] == "member@test.example.org"]
        assert sent and "Amount:    $50.00" in sent[-1]["body"]
        assert "Paid:      $400.00" in sent[-1]["body"]
        page = member_client.get(f"/pay/registration/{token}")
        assert b"Already paid" not in page.data

    def test_a_paid_workshop_cannot_be_withdrawn_from_the_form(
            self, seeded, member_client, app, conf, mailbox):
        from app.models import record_payment_event
        _set(app, conf["se_id"], tentative=False)
        _register(member_client, conf, "yes")
        with app.app_context():
            reg = Registration.query.filter_by(conference_id=conf["id"]).one()
            record_payment_event(registration_id=reg.id, transaction_id="T-ALL",
                                 merchant_reference=f"reg_{reg.id}",
                                 event_type="payment.captured", amount=45000)
            reg.status = "paid"
            db.session.commit()
        db.session.expire_all()
        page = member_client.get(f"/conferences/{conf['slug']}/register")
        assert b"Paid. To withdraw, please contact the organisers." in page.data
        _register(member_client, conf, "no")
        with app.app_context():
            reg = Registration.query.filter_by(conference_id=conf["id"]).one()
            assert reg.sub_events["emcr_workshop"]["attending"] is True
            assert reg.status == "paid" and reg.amount_due == 0


# --- starting to bill a tentative workshop ----------------------------------

def _code(mailbox) -> str:
    return re.search(r"Confirmation code: (\d{6})", mailbox[-1]["body"]).group(1)


class TestStartingBilling:
    @pytest.fixture
    def attendees(self, app, conf):
        tag = secrets.token_hex(3)
        return {
            "paid": _reg(app, conf, email=f"paid-{tag}@example.org",
                         status="paid", paid=40000),
            "pending": _reg(app, conf, email=f"pend-{tag}@example.org",
                            status="pending"),
            "declined": _reg(app, conf, email=f"no-{tag}@example.org",
                             status="paid", paid=40000, attending=False),
            "cancelled": _reg(app, conf, email=f"cx-{tag}@example.org",
                              status="cancelled"),
        }

    def test_the_page_lists_who_would_be_charged_and_changes_nothing(
            self, seeded, admin_client, app, conf, attendees, mailbox):
        page = admin_client.get(f"/admin/financial/sub-events/{conf['se_id']}/billing")
        text = page.get_data(as_text=True)
        assert "Registrations to be charged (2)" in text
        assert "cannot be undone" in text
        with app.app_context():
            assert db.session.get(Registration, attendees["paid"]).amount_due == 0
        assert mailbox == []

    def test_nothing_happens_without_the_code(
            self, seeded, admin_client, app, conf, attendees, mailbox):
        admin_client.post(f"/admin/financial/sub-events/{conf['se_id']}/billing/request",
                          data={"send_invoices": "1", "note": "It is going ahead."})
        assert [m["to"] for m in mailbox] == ["admin@test.example.org"]
        page = admin_client.post(
            f"/admin/financial/sub-events/{conf['se_id']}/billing/confirm",
            data={"code": "000000"}, follow_redirects=True)
        assert b"match, or it has expired" in page.data
        with app.app_context():
            from app.models import SubEvent
            assert db.session.get(SubEvent, conf["se_id"]).tentative is True
            assert db.session.get(Registration, attendees["paid"]).amount_due == 0
        assert len(mailbox) == 1

    def test_the_code_charges_attendees_and_emails_them(
            self, seeded, admin_client, app, conf, attendees, mailbox):
        note = "Good news: the EMCR Workshop is going ahead."
        admin_client.post(f"/admin/financial/sub-events/{conf['se_id']}/billing/request",
                          data={"send_invoices": "1", "note": note})
        admin_client.post(f"/admin/financial/sub-events/{conf['se_id']}/billing/confirm",
                          data={"code": _code(mailbox)}, follow_redirects=True)
        with app.app_context():
            from app.models import SubEvent
            assert db.session.get(SubEvent, conf["se_id"]).tentative is False
            paid = db.session.get(Registration, attendees["paid"])
            assert paid.status == "pending" and paid.amount_due == 5000
            pending = db.session.get(Registration, attendees["pending"])
            assert pending.amount_due == 45000
            assert db.session.get(Registration, attendees["declined"]).amount_due == 0
            assert db.session.get(Registration, attendees["declined"]).status == "paid"
            assert db.session.get(Registration, attendees["cancelled"]).amount_due == 40000
            emails = {db.session.get(Registration, attendees[k]).user.email: k
                      for k in attendees}
        invoices = [m for m in mailbox if m["subject"].startswith("Invoice")]
        assert sorted(emails[m["to"]] for m in invoices) == ["paid", "pending"]
        for m in invoices:
            assert m["body"].startswith(note)
            assert "EMCR Workshop — $50.00" in m["body"]
            assert m["attachments"][0][1].startswith(b"%PDF")

    def test_billing_without_emails_sends_none(
            self, seeded, admin_client, app, conf, attendees, mailbox):
        admin_client.post(f"/admin/financial/sub-events/{conf['se_id']}/billing/request",
                          data={"note": ""})
        admin_client.post(f"/admin/financial/sub-events/{conf['se_id']}/billing/confirm",
                          data={"code": _code(mailbox)})
        assert [m["to"] for m in mailbox] == ["admin@test.example.org"]
        with app.app_context():
            assert db.session.get(Registration, attendees["paid"]).amount_due == 5000

    def test_it_happens_once(self, seeded, admin_client, app, conf, attendees,
                             mailbox):
        admin_client.post(f"/admin/financial/sub-events/{conf['se_id']}/billing/request",
                          data={"note": ""})
        code = _code(mailbox)
        admin_client.post(f"/admin/financial/sub-events/{conf['se_id']}/billing/confirm",
                          data={"code": code})
        page = admin_client.get(f"/admin/financial/sub-events/{conf['se_id']}/billing",
                                follow_redirects=True)
        assert b"already billed" in page.data
        with app.app_context():
            assert db.session.get(Registration, attendees["paid"]).amount_due == 5000

    def test_members_cannot_reach_it(self, seeded, member_client, conf):
        assert member_client.get(
            f"/admin/financial/sub-events/{conf['se_id']}/billing").status_code == 403


# --- documents and payment --------------------------------------------------

class TestDocumentsAndPayment:
    def _billed_paid_reg(self, app, conf):
        """Paid the tier, then billed the workshop: owes $50."""
        from app.services.sub_event_billing import start_billing
        from app.models import SubEvent
        rid = _reg(app, conf, email=f"d-{secrets.token_hex(3)}@example.org",
                   status="paid", paid=40000)
        with app.app_context():
            start_billing(db.session.get(SubEvent, conf["se_id"]), send_invoices=False)
        return rid

    def test_the_invoice_lists_the_workshop_and_what_was_paid(
            self, seeded, app, conf, monkeypatch):
        from app.services import documents as docs
        from app.services.invoice import registration_document
        seen = []
        monkeypatch.setattr(docs, "_compile",
                            lambda t, j, tex, *a, **k: seen.append(tex.read_text()) or b"%PDF")
        rid = self._billed_paid_reg(app, conf)
        with app.app_context():
            registration_document(db.session.get(Registration, rid), "invoice")
        tex = seen[-1]
        # Numbered by the registration, not by the payment already made.
        assert f"\\mbox{{REG-{rid:06d}}}" in tex and f"TXN-{rid}" not in tex
        assert "Physics 2026 Standard & 1 & 400.00" in tex
        assert "EMCR Workshop & 1 & 50.00" in tex
        assert "Paid previously" in tex and "400.00" in tex
        assert re.search(r"Amount Due.*?50\.00", tex, re.S)

    def test_paying_the_balance_sends_a_receipt_for_the_balance(
            self, seeded, client, app, conf, monkeypatch, mailbox):
        from app.services.gateways import WebhookResult
        rid = self._billed_paid_reg(app, conf)

        class _Gateway:
            def verify_webhook(self, body, headers):
                return WebhookResult(success=True, registration_id=rid,
                                     transaction_id="TXN-BALANCE",
                                     event_type="payment.captured",
                                     merchant_reference=f"reg_{rid}-x", amount=5000)

        monkeypatch.setattr("app.services.payments._active_gateway", lambda: _Gateway())
        client.post("/payments/webhook", data=b"{}", content_type="application/json")
        with app.app_context():
            from app.models import IssuedDocument
            reg = db.session.get(Registration, rid)
            assert reg.status == "paid" and reg.amount_due == 0
            receipt = (IssuedDocument.query.filter_by(kind="receipt")
                       .order_by(IssuedDocument.id.desc()).first())
            v = json.loads(receipt.vars_json)
        assert v["amount"] == "50.00"
        assert v["prior_paid"] == "400.00"
        assert json.loads(v["line_items"])[1] == ["EMCR Workshop", 5000]

    def test_reconciling_does_not_repay_an_old_capture(
            self, seeded, app, conf, monkeypatch, mailbox):
        """The registration's known payment is the tier, already credited;
        hearing about it again must not mark the new balance paid."""
        from app.models.content import get_active_payment_gateway  # noqa: F401
        from app.services.gateways import PaymentStatus
        rid = self._billed_paid_reg(app, conf)

        class _Gateway:
            def get_payment_status(self, txn):
                return PaymentStatus(raw_status="CAPTURED", transaction_id=txn,
                                     amount=40000)

            def get_checkout_payment(self, txn):
                return PaymentStatus(raw_status="CREATED", transaction_id=txn)

        monkeypatch.setattr("app.services.payments._active_gateway", lambda: _Gateway())
        monkeypatch.setattr("app.services.reconcile._reconcile_test_payments",
                            lambda g: (0, [], [], []))
        with app.app_context():
            from app.services.reconcile import reconcile_payments
            reconcile_payments()
            reg = db.session.get(Registration, rid)
            assert reg.status == "pending" and reg.amount_due == 5000
            email = reg.user.email
        # The sweep covers every unsettled registration in the shared test
        # database; only this one's mail is this test's business.
        assert [m for m in mailbox if m["to"] == email] == []


# --- the admin list ---------------------------------------------------------

def test_the_registration_list_shows_and_filters_by_workshop(
        seeded, admin_client, app, conf):
    tag = secrets.token_hex(3)
    yes = _reg(app, conf, email=f"yes-{tag}@example.org", status="pending")
    no = _reg(app, conf, email=f"nah-{tag}@example.org", status="pending",
              attending=False)
    page = admin_client.get(f"/admin/registrations?conference_id={conf['id']}"
                            f"&sub_event={conf['se_id']}").get_data(as_text=True)
    assert f"yes-{tag}@example.org" in page and f"nah-{tag}@example.org" not in page
    assert "tentative" in page
    assert yes and no


# --- the migration ----------------------------------------------------------

def test_existing_prices_become_cents_and_events_tentative_once(tmp_path):
    """Run once over a database from before the change, and never again — a
    replay after a restore must not multiply prices by a hundred twice."""
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    db_path = tmp_path / "app.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}",
           "FLASK_APP": "wsgi:app", "SECRET_KEY": "test-secret-for-migrations"}

    def run(*args):
        r = subprocess.run([sys.executable, *args], cwd=root, env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr

    run("-m", "flask", "db", "upgrade", "9b3e61d0c7a4")
    c = sqlite3.connect(db_path)
    # The baseline builds tables from the current models; a deployed database
    # from before this change has no such column.
    c.execute("alter table sub_events drop column tentative")
    # Foreign keys are not enforced on this connection, so the row needs no
    # conference to belong to.
    c.execute("insert into sub_events (conference_id, name, price, display_order) "
              "values (1, 'EMCR Workshop', 50, 0)")
    c.commit()
    run("-m", "flask", "db", "upgrade")
    assert c.execute("select price, tentative from sub_events").fetchone() == (5000, 1)

    run("-m", "flask", "db", "stamp", "9b3e61d0c7a4")
    run("-m", "flask", "db", "upgrade")
    assert c.execute("select price, tentative from sub_events").fetchone() == (5000, 1)
    c.close()
