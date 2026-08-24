"""Regressions for the pre-go-live payment audit (2026-08-19).

Each test pins one defect found in that review, so the fix cannot quietly
regress: duplicate credits, the edit-during-payment race, settled-state
guards, per-link throttling, and amount parsing.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import Conference, PaymentEvent, PriceTier, Registration, User


def _registration(app, *, amount=40000, status="pending", email=None):
    import secrets
    tag = secrets.token_hex(4)
    with app.app_context():
        u = User(email=email or f"payer-{tag}@example.org", full_name="Pat Payer",
                 role_name="member")
        db.session.add(u)
        db.session.flush()
        c = Conference(slug=f"pay-conf-{tag}", title="Physics 2026",
                       start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
        db.session.add(c)
        db.session.flush()
        db.session.add(PriceTier(conference_id=c.id, name="Standard",
                                 amount=amount))
        r = Registration(user_id=u.id, conference_id=c.id, tier_name="Standard",
                         amount=amount, status=status)
        db.session.add(r)
        db.session.commit()
        r.charge_to(amount, reason="Standard")
        return r.id, c.slug


class TestDuplicateCredits:
    """One payment must move the balance once, however many times it lands."""

    def test_redelivered_capture_credits_once(self, seeded, app):
        rid, _ = _registration(app)
        with app.app_context():
            from app.models import record_payment_event
            for _ in range(3):                      # webhook retried twice
                record_payment_event(
                    transaction_id="PAY-1", merchant_reference=f"reg_{rid}",
                    registration_id=rid, event_type="payment.captured",
                    amount=40000, note="capture")
            reg = Registration.query.get(rid)
            assert reg.amount_outstanding == 0
            # Every delivery is still on the ledger — the record is the point.
            assert PaymentEvent.query.filter_by(
                registration_id=rid, event_type="payment.captured").count() == 3

    def test_paid_and_captured_for_one_payment_credit_once(self, seeded, app):
        """Both verbs describe the same capture under the same transaction id."""
        rid, _ = _registration(app)
        with app.app_context():
            from app.models import record_payment_event
            for etype in ("payment.captured", "payment.paid"):
                record_payment_event(
                    transaction_id="PAY-2", merchant_reference=f"reg_{rid}",
                    registration_id=rid, event_type=etype, amount=40000)
            assert Registration.query.get(rid).amount_outstanding == 0

    def test_two_real_payments_still_credit_twice(self, seeded, app):
        """Deduplication must not hide a genuine double payment."""
        rid, _ = _registration(app)
        with app.app_context():
            from app.models import record_payment_event
            for txn in ("PAY-A", "PAY-B"):
                record_payment_event(
                    transaction_id=txn, merchant_reference=f"reg_{rid}",
                    registration_id=rid, event_type="payment.captured",
                    amount=40000)
            assert Registration.query.get(rid).amount_outstanding == -40000

    def test_toggling_paid_does_not_stack_credits(self, seeded, app, admin_client):
        """A correction — paid, back to pending, paid again — is routine, and
        used to credit the full fee on every pass."""
        rid, _ = _registration(app)
        for status in ("paid", "pending", "paid"):
            admin_client.post(f"/admin/registrations/{rid}/status",
                              data={"status": status}, follow_redirects=True)
        with app.app_context():
            assert Registration.query.get(rid).amount_outstanding == 0

    def test_manual_settlement_credits_only_the_balance(self, seeded, app,
                                                        admin_client):
        """Part paid by card, the rest by transfer: the manual line must settle
        what is left, not the whole fee again."""
        rid, _ = _registration(app)
        with app.app_context():
            from app.models import record_payment_event
            record_payment_event(
                transaction_id="PAY-3", merchant_reference=f"reg_{rid}",
                registration_id=rid, event_type="payment.captured", amount=15000)
            assert Registration.query.get(rid).amount_outstanding == 25000

        admin_client.post(f"/admin/registrations/{rid}/status",
                          data={"status": "paid"}, follow_redirects=True)
        with app.app_context():
            assert Registration.query.get(rid).amount_outstanding == 0
            manual = PaymentEvent.query.filter_by(
                registration_id=rid, event_type="manual.paid").one()
            assert manual.amount == 25000


class TestEditLockWhilePaying:
    """A checkout is minted against the amount owed at that moment."""

    def _start_checkout(self, app, rid):
        with app.app_context():
            from app.models import record_payment_event
            record_payment_event(
                transaction_id="CHK-1", merchant_reference=f"reg_{rid}-x1",
                registration_id=rid, event_type="checkout.created", amount=40000)

    def test_registration_cannot_be_changed_mid_payment(self, seeded, app, client):
        rid, slug = _registration(app)
        with app.app_context():
            uid = Registration.query.get(rid).user_id
        with client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        self._start_checkout(app, rid)

        resp = client.post(f"/conferences/{slug}/register",
                           data={"tier": "Standard"}, follow_redirects=True)
        assert b"payment for this registration is in progress" in resp.data

    def test_lock_lifts_when_the_session_expires(self, seeded, app):
        """An abandoned checkout must not freeze the registration for good."""
        from app.blueprints.member import payment_in_flight
        rid, _ = _registration(app)
        self._start_checkout(app, rid)
        with app.app_context():
            reg = Registration.query.get(rid)
            assert payment_in_flight(reg) is not None
            evt = PaymentEvent.query.filter_by(
                registration_id=rid, event_type="checkout.created").one()
            evt.created_at = datetime.utcnow() - timedelta(hours=3)
            db.session.commit()
            assert payment_in_flight(Registration.query.get(rid)) is None

    def test_lock_lifts_once_the_payment_lands(self, seeded, app):
        from app.blueprints.member import payment_in_flight
        from app.models import record_payment_event
        rid, _ = _registration(app)
        self._start_checkout(app, rid)
        with app.app_context():
            record_payment_event(
                transaction_id="PAY-4", merchant_reference=f"reg_{rid}-x1",
                registration_id=rid, event_type="payment.captured", amount=40000)
            assert payment_in_flight(Registration.query.get(rid)) is None


class TestSettledGuards:
    def test_member_cannot_check_out_a_refunded_registration(self, seeded, app,
                                                             client):
        rid, _ = _registration(app, status="refunded")
        with app.app_context():
            uid = Registration.query.get(rid).user_id
        with client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        resp = client.post(f"/pay/{rid}/checkout", follow_redirects=True)
        assert b"already refunded" in resp.data.lower()


class TestPerLinkThrottle:
    def test_budget_is_per_link_not_per_client(self, seeded, app):
        """One link being hammered must not spend another link's budget."""
        from app.models.rate_limit import allow
        with app.app_context():
            for _ in range(5):
                assert allow("t.view", "link-a", limit=5, per_seconds=3600)
            assert not allow("t.view", "link-a", limit=5, per_seconds=3600)
            # A different link is unaffected — the failure mode of an IP-keyed
            # limiter behind a proxy, where every payer shares one bucket.
            assert allow("t.view", "link-b", limit=5, per_seconds=3600)

    def test_window_expires(self, seeded, app):
        from app.models.rate_limit import RateBucket, allow
        with app.app_context():
            assert allow("t.view", "link-c", limit=1, per_seconds=3600)
            assert not allow("t.view", "link-c", limit=1, per_seconds=3600)
            b = RateBucket.query.filter_by(key="t.view:link-c").one()
            b.window_start = datetime.utcnow() - timedelta(hours=2)
            db.session.commit()
            assert allow("t.view", "link-c", limit=1, per_seconds=3600)

    def test_pay_page_throttles_on_the_token(self, seeded, app, client):
        rid, _ = _registration(app)
        with app.app_context():
            token = Registration.query.get(rid).ensure_pay_token()
        seen_limit = False
        for _ in range(25):
            resp = client.get(f"/pay/registration/{token}")
            if b"Too many attempts" in resp.data:
                seen_limit = True
                break
        assert seen_limit


class TestAmountParsing:
    @pytest.mark.parametrize("bad", ["-50", "1e5", "nan", "inf", "2000000", "abc"])
    def test_rejects_unusable_amounts(self, bad):
        from app.services.jinja_filters import parse_cents
        with pytest.raises(ValueError):
            parse_cents(bad)

    @pytest.mark.parametrize("good,cents", [("50", 5000), ("50.00", 5000),
                                            ("$1,234.50", 123450), ("", 0)])
    def test_accepts_ordinary_amounts(self, good, cents):
        from app.services.jinja_filters import parse_cents
        assert parse_cents(good) == cents


class TestThePriceIsStruckOnce:
    """What someone was charged is a fact about their registration, not
    something to re-derive from today's date on every save."""

    def _conference(self, app, *, early_bird_gone):
        """A conference with a $300 early-bird rate and a $450 standard one."""
        import secrets
        tag = secrets.token_hex(4)
        deadline = (date.today() - timedelta(days=1) if early_bird_gone
                    else date.today() + timedelta(days=30))
        with app.app_context():
            c = Conference(slug=f"eb-{tag}", title="Early Bird 2027",
                           start_date=date(2027, 5, 1), end_date=date(2027, 5, 3),
                           is_accepting_registrations=True,
                           early_bird_deadline=deadline)
            db.session.add(c)
            db.session.flush()
            db.session.add(PriceTier(conference_id=c.id, name="Standard",
                                     amount=45000, early_bird_amount=30000))
            db.session.commit()
            return c.slug

    def _register(self, client, slug, **extra):
        data = {"tier": "Standard", "dietary": "", "accessibility": ""}
        data.update(extra)
        return client.post(f"/conferences/{slug}/register", data=data,
                           follow_redirects=True)

    def test_editing_after_the_early_bird_lapses_does_not_rebill(
            self, seeded, member_client, app, monkeypatch):
        """The bug: a member who paid the early-bird rate and later corrected a
        dietary note was silently re-priced at the full rate and billed the
        difference — for a registration they had paid in full."""
        slug = self._conference(app, early_bird_gone=False)
        self._register(member_client, slug)

        with app.app_context():
            reg = Registration.query.filter_by(tier_name="Standard").order_by(
                Registration.id.desc()).first()
            rid = reg.id
            assert reg.amount == 30000, "should be struck at the early-bird rate"
            from app.models import record_payment_event
            record_payment_event(transaction_id="PAY-EB",
                                 merchant_reference=f"reg_{rid}",
                                 registration_id=rid,
                                 event_type="payment.captured", amount=30000,
                                 note="paid in full")
            assert Registration.query.get(rid).amount_outstanding == 0
            conf_id = reg.conference_id

        # The early-bird deadline passes, then the member edits a dietary note.
        with app.app_context():
            c = Conference.query.get(conf_id)
            c.early_bird_deadline = date.today() - timedelta(days=1)
            db.session.commit()
        self._register(member_client, slug, dietary="No shellfish")

        with app.app_context():
            reg = Registration.query.get(rid)
            assert reg.dietary == "No shellfish", "the edit must still land"
            assert reg.amount == 30000, "the struck price must not move"
            assert reg.amount_outstanding == 0, "nothing further may be owed"
            charges = PaymentEvent.query.filter_by(
                registration_id=rid,
                event_type="registration.payment_due").count()
            assert charges == 1, "the edit must not add a second charge line"

    def test_changing_tier_still_reprices_at_todays_rate(self, seeded,
                                                         member_client, app):
        """Holding the price must not freeze it against a real tier change."""
        slug = self._conference(app, early_bird_gone=True)
        with app.app_context():
            c = Conference.query.filter_by(slug=slug).first()
            db.session.add(PriceTier(conference_id=c.id, name="Student",
                                     amount=15000))
            db.session.commit()

        self._register(member_client, slug, tier="Student")
        with app.app_context():
            reg = Registration.query.filter_by(tier_name="Student").order_by(
                Registration.id.desc()).first()
            rid, = (reg.id,)
            assert reg.amount == 15000

        self._register(member_client, slug, tier="Standard")
        with app.app_context():
            reg = Registration.query.get(rid)
            assert reg.tier_name == "Standard"
            assert reg.amount == 45000, "early bird has lapsed — full rate"
            assert reg.amount_outstanding == 45000


class TestManualReversal:
    """A refund restores what was received, not what was outstanding."""

    def test_marking_a_paid_registration_refunded_records_the_money_back(
            self, seeded, admin_client, app):
        """The bug: a paid registration owes nothing, so recording the refund
        against the balance recorded a refund of zero — the society handed the
        money back and its ledger went on saying it had kept it."""
        rid, _ = _registration(app, amount=40000)
        with app.app_context():
            from app.models import record_payment_event
            record_payment_event(transaction_id="PAY-9",
                                 merchant_reference=f"reg_{rid}",
                                 registration_id=rid,
                                 event_type="payment.captured", amount=40000,
                                 note="paid")
            reg = Registration.query.get(rid)
            reg.status = "paid"
            db.session.commit()
            assert reg.amount_outstanding == 0

        admin_client.post(f"/admin/registrations/{rid}/status",
                          data={"status": "refunded"}, follow_redirects=True)

        with app.app_context():
            evt = (PaymentEvent.query
                   .filter_by(registration_id=rid, event_type="manual.refunded")
                   .first())
            assert evt is not None, "the reversal must reach the ledger"
            assert evt.amount == 40000, "it must carry what was received"
            reg = Registration.query.get(rid)
            assert reg.amount_outstanding == 40000, (
                "the money is back on the books, not forgiven")


class TestCancelledThenPaid:
    """Backing out of the gateway and trying again is ordinary.

    The bug: the capture fell through to the double-payment branch, so a fully
    paid registration went on reading `cancelled`, the pay link refused it, and
    admins were emailed about a duplicate that never existed.
    """

    def test_a_capture_settles_a_cancelled_registration(self, seeded, client,
                                                        monkeypatch, app):
        from app.services.gateways import WebhookResult

        rid, _ = _registration(app, amount=40000, status="cancelled")
        with app.app_context():
            reg = Registration.query.get(rid)
            reg.transaction_id = "PAY-ABANDONED"
            db.session.commit()

        class _Gateway:
            def verify_webhook(self, body, headers):
                return WebhookResult(
                    success=True, registration_id=rid,
                    transaction_id="PAY-SECOND-TRY",
                    event_type="payment.captured",
                    merchant_reference=f"reg_{rid}", amount=40000)

        monkeypatch.setattr("app.services.payments._active_gateway",
                            lambda: _Gateway())
        resp = client.post("/payments/webhook", data=b"{}",
                           content_type="application/json")
        assert resp.status_code == 200

        with app.app_context():
            reg = Registration.query.get(rid)
            assert reg.status == "paid", (
                "a successful capture must settle a cancelled attempt")
            assert reg.amount_outstanding == 0
            assert reg.transaction_id == "PAY-SECOND-TRY"


class TestResendPaymentEmail:
    """A payment link otherwise goes out only when a member saves their
    registration, and only if the portal is open at that moment."""

    @pytest.fixture
    def mailbox(self, monkeypatch):
        box: list[dict] = []

        def _record(**kw):
            box.append(kw)
            return True

        monkeypatch.setattr("app.services.mail.send_mail", _record)
        # payments binds send_mail at import.
        monkeypatch.setattr("app.services.payments.send_mail", _record)
        return box

    @pytest.fixture
    def portal_open(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.payments.payments_open_to_members", lambda: True)

    def _send(self, admin_client, rid, **data):
        return admin_client.post(
            f"/admin/registrations/{rid}/resend-payment-email",
            data=data, follow_redirects=True)

    def _count(self, app, rid):
        from app.models.payment_event import payment_email_counts
        with app.app_context():
            return payment_email_counts([rid]).get(rid, 0)

    def test_it_sends_and_counts_each_ask(self, seeded, admin_client, app,
                                          mailbox, portal_open):
        rid, _ = _registration(app, amount=40000)
        assert self._count(app, rid) == 0

        self._send(admin_client, rid)
        assert self._count(app, rid) == 1
        with app.app_context():
            assert Registration.query.get(rid).payment_sent_at is not None

        self._send(admin_client, rid)
        assert self._count(app, rid) == 2
        assert len(mailbox) == 2

    def test_asking_does_not_move_the_balance(self, seeded, admin_client, app,
                                              mailbox, portal_open):
        rid, _ = _registration(app, amount=40000)
        self._send(admin_client, rid)
        self._send(admin_client, rid)
        with app.app_context():
            assert Registration.query.get(rid).amount_outstanding == 40000

    def test_a_settled_registration_is_not_chased(self, seeded, admin_client,
                                                  app, mailbox, portal_open):
        rid, _ = _registration(app, amount=40000)
        with app.app_context():
            from app.models import record_payment_event
            record_payment_event(transaction_id="PAY-DONE",
                                 merchant_reference=f"reg_{rid}",
                                 registration_id=rid,
                                 event_type="payment.captured", amount=40000)
            reg = Registration.query.get(rid)
            reg.status = "paid"
            db.session.commit()

        self._send(admin_client, rid)
        assert mailbox == []

    def test_a_closed_portal_refuses_without_an_override(self, seeded,
                                                         admin_client, app,
                                                         mailbox, monkeypatch):
        monkeypatch.setattr(
            "app.services.payments.payments_open_to_members", lambda: False)
        rid, _ = _registration(app, amount=40000)

        resp = self._send(admin_client, rid)
        assert b"Member payments are closed" in resp.data
        assert mailbox == []
        assert self._count(app, rid) == 0

    def test_a_closed_portal_sends_with_the_override(self, seeded, admin_client,
                                                     app, mailbox, monkeypatch):
        monkeypatch.setattr(
            "app.services.payments.payments_open_to_members", lambda: False)
        rid, _ = _registration(app, amount=40000)

        self._send(admin_client, rid, anyway="1")
        assert len(mailbox) == 1
        assert self._count(app, rid) == 1

    def test_a_failed_send_records_nothing(self, seeded, admin_client, app,
                                           monkeypatch, portal_open):
        """Recording it would say the payer was contacted when they were not."""
        monkeypatch.setattr("app.services.payments.send_mail",
                            lambda **kw: False)
        rid, _ = _registration(app, amount=40000)

        self._send(admin_client, rid)
        assert self._count(app, rid) == 0
        with app.app_context():
            assert Registration.query.get(rid).payment_sent_at is None

    def test_it_needs_the_finance_permission(self, seeded, member_client, app,
                                             mailbox, portal_open):
        rid, _ = _registration(app, amount=40000)
        resp = member_client.post(
            f"/admin/registrations/{rid}/resend-payment-email")
        assert resp.status_code in (302, 403, 404)
        assert mailbox == []
