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
        """Transactions group by the platform transaction ID when known,
        falling back to the merchant reference (e.g. before checkout
        completes, or for manually sent invoices)."""
        return self.transaction_id or self.merchant_reference or "(unreferenced)"


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
    except Exception:
        log.exception("Failed to record payment event %r", event_type)
        try:
            db.session.rollback()
        except Exception:
            pass
