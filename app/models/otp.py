"""One-time-password codes for email login + destructive-action confirmation."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class OTPCode(db.Model):
    __tablename__ = "otp_codes"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), index=True, nullable=False)
    code = db.Column(db.String(6), nullable=False)

    # Why this code was issued. Important: a login OTP must not authorise a
    # destructive admin action and vice versa.
    purpose = db.Column(db.String(32), default="login", nullable=False, index=True)

    expires_at = db.Column(db.DateTime, nullable=False)
    consumed_at = db.Column(db.DateTime, nullable=True)
    attempt_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ip = db.Column(db.String(64), nullable=True)

    @property
    def consumed(self) -> bool:
        return self.consumed_at is not None

    def is_valid(self) -> bool:
        return not self.consumed and datetime.utcnow() < self.expires_at
