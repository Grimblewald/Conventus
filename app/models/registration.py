"""Conference registrations attached to a user + tier."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db




class Registration(db.Model):
    __tablename__ = "registrations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conferences.id"),
                              nullable=False, index=True)

    tier_name = db.Column(db.String(80), nullable=False)
    amount = db.Column(db.Integer, default=0, nullable=False)

    dietary = db.Column(db.String(200), default="")
    accessibility = db.Column(db.String(400), default="")
    custom_data = db.Column(db.JSON, default=None)
    sub_events = db.Column(db.JSON, default=None)
    status = db.Column(db.String(40), default="pending", nullable=False)
    payment_sent_at = db.Column(db.DateTime, nullable=True)
    transaction_id = db.Column(db.String(120), nullable=True)
    last_webhook_event = db.Column(db.String(80), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)

    conference = db.relationship("Conference")

    @property
    def reference(self) -> str:
        """The registration's payer-facing reference, e.g. REG-000123.

        Derived from the id rather than stored: it must exist the moment the
        registration does, because a member paying by bank transfer needs
        something to quote long before any card checkout mints a merchant
        reference. Deriving it also means it can never drift, be blank, or
        collide — the id already guarantees all three.

        Distinct from `transaction_id` (the gateway's per-operation id) and
        from the checkout's merchant reference (reg_<id>-c<conf>u<user>-<hex>,
        minted at checkout and absent until then).
        """
        return f"REG-{self.id:06d}"

    @property
    def sanitized_reference(self) -> str:
        """`reference` as it survives a bank reference field — see
        app.services.invoice.sanitized_reference for why the punctuation goes.
        """
        return self.reference.replace("-", "")
