"""Per-transaction financial event ledger.

Every verified gateway webhook event, checkout creation, and manually
sent invoice is appended here so the admin can reconstruct the full
history of a transaction. Rows are never updated or deleted.
"""
from __future__ import annotations

import logging
from datetime import datetime

from ..extensions import db

log = logging.getLogger(__name__)


class PaymentEvent(db.Model):
    __tablename__ = "payment_events"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)
    transaction_id = db.Column(db.String(120), default="", index=True)
    merchant_reference = db.Column(db.String(120), default="", index=True)
    registration_id = db.Column(db.Integer, db.ForeignKey("registrations.id"),
                                nullable=True, index=True)
    event_type = db.Column(db.String(80), default="")
    amount = db.Column(db.Integer, nullable=True)
    note = db.Column(db.String(400), default="")

    registration = db.relationship(
        "Registration", backref=db.backref("payment_events", lazy="dynamic"))

    @property
    def group_key(self) -> str:
        """Group by merchant reference — the stable key across a payment's
        whole lifecycle. Worldline assigns each operation (authorization,
        void, refund) its own transaction ID, so grouping by transaction ID
        would split one payment's history into several groups."""
        return self.merchant_reference or self.transaction_id or "(unreferenced)"


# --- What each event does to the balance -----------------------------------
#
# The ledger already carried every credit — payments, refunds, manual
# settlements. What it never carried was the debit: nothing recorded that a
# registration had been *charged* anything, so `Registration.amount` was the
# only record of it, and that is overwritten on every edit. Charge lines close
# that gap, and `registration.no_payment_due` was already one of them — a
# charge that happens to be zero.
#
# checkout.created is deliberately weightless. It carries the full amount and
# is written on every attempt, so three abandoned checkouts would otherwise
# read as three charges.
CHARGE_EVENTS = ("registration.payment_due", "registration.no_payment_due")

_SETTLES = ("captured", "paid")
_REVERSES = ("refunded",)
_MONEY_NAMESPACES = ("payment", "manual", "reconcile", "refund")


def event_delta(event_type: str, amount: int | None) -> int:
    """What this event adds to (or takes off) the amount outstanding.

    Positive raises the balance owing, negative reduces it. The sign comes
    from the event type alone, which is what lets one function serve gateway
    webhooks, manual settlements, reconciliation and tier changes alike.
    """
    if not amount:
        return 0
    if event_type in CHARGE_EVENTS:
        return amount           # a tier change may charge a negative delta
    namespace, _, verb = (event_type or "").partition(".")
    if namespace in _MONEY_NAMESPACES:
        if verb in _SETTLES:
            return -amount
        if verb in _REVERSES:
            return amount       # a refund puts the balance back on the books
    return 0


def _money_rows(registration_id: int):
    """This registration's ledger rows, with each gateway operation counted
    once (see `deduplicated`)."""
    rows = (PaymentEvent.query
            .filter(PaymentEvent.registration_id == registration_id)
            .order_by(PaymentEvent.id.asc())
            .with_entities(PaymentEvent.event_type, PaymentEvent.amount,
                           PaymentEvent.transaction_id)
            .all())
    return deduplicated(rows)


def deduplicated(rows):
    """Drop repeat deliveries of one gateway operation, keeping the first.

    One payment can reach the ledger more than once: webhooks retry until they
    are acknowledged, and a single payment may emit both `payment.paid` and
    `payment.captured`. Every arrival is recorded — the ledger is append-only
    and being able to see the redelivery is the point — but only the first may
    move the balance, or a member who paid once is credited twice.

    Keyed on the gateway's transaction id and the direction of the movement,
    not the event name, because the two verbs above describe one capture under
    one id. Rows without a transaction id (charge lines, manual settlements)
    are deliberate local acts, never redeliveries, so they always count.
    """
    seen: set[tuple[str, int]] = set()
    kept = []
    for event_type, amount, txn in rows:
        delta = event_delta(event_type, amount)
        if txn and delta:
            key = (txn, 1 if delta > 0 else -1)
            if key in seen:
                continue
            seen.add(key)
        kept.append((event_type, amount))
    return kept


def outstanding_for(registration_id: int) -> int:
    """The amount still owed on a registration, from the ledger alone."""
    return sum(event_delta(t, a) for t, a in _money_rows(registration_id))


def amount_received(registration_id: int) -> int:
    """Money actually received against a registration, net of refunds.

    The question "has anyone paid for this?" has to be answered from events
    that moved money, not from evidence that somebody once tried. A cancelled
    checkout leaves both a `payment.cancelled` row and a transaction id on the
    registration, and treating either as settlement is how a registration with
    an abandoned attempt in its past kept a paid status it had not earned.
    """
    return -sum(event_delta(t, a) for t, a in _money_rows(registration_id)
                if t not in CHARGE_EVENTS)


def recompute_outstanding(registration_id: int) -> int | None:
    """Refresh the stored balance for a registration and return it.

    Called wherever an event lands, so the number maintains itself rather than
    being recalculated — differently — at each of the places that read it.
    """
    from .registration import Registration

    reg = db.session.get(Registration, registration_id)
    if reg is None:
        return None
    reg.amount_outstanding = outstanding_for(registration_id)
    db.session.commit()
    return reg.amount_outstanding


def record_payment_event(*, transaction_id: str = "", merchant_reference: str = "",
                         registration_id: int | None = None, event_type: str = "",
                         amount: int | None = None, note: str = "") -> None:
    """Best-effort append to the financial event ledger. Never raises."""
    try:
        db.session.add(PaymentEvent(
            transaction_id=transaction_id or "",
            merchant_reference=merchant_reference or "",
            registration_id=registration_id,
            event_type=event_type or "",
            amount=amount,
            note=(note or "")[:400],
        ))
        db.session.commit()
        # Every registration-linked event funnels through here, so this is the
        # one place the balance has to be kept current.
        if registration_id:
            recompute_outstanding(registration_id)
    except Exception:
        log.exception("Failed to record payment event %r", event_type)
        try:
            db.session.rollback()
        except Exception:
            pass
