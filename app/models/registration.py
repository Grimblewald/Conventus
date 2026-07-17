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
