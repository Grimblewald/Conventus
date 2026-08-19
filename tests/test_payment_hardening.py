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
