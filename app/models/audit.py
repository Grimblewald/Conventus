"""Audit log — append-only record of admin / committee actions."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                         nullable=True, index=True)
    actor_email = db.Column(db.String(255))  # snapshot — survives user deletion
    actor_role = db.Column(db.String(32))

    # Verb in dotted form, e.g. "user.role_changed", "site.palette_updated",
    # "conference.deleted", "abstract.decided".
    action = db.Column(db.String(80), nullable=False, index=True)

    # What the action targeted (model + id), free text.
    target_kind = db.Column(db.String(40))
    target_id = db.Column(db.String(80))

    # Short human summary + optional JSON-ish metadata as text.
    summary = db.Column(db.String(400))
    metadata_json = db.Column(db.Text)

    ip = db.Column(db.String(64))

    actor = db.relationship("User", foreign_keys=[actor_id])
