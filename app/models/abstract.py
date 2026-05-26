"""Abstract submissions (1 optional figure each)."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


ABSTRACT_STATUSES = ("submitted", "accepted", "rejected", "revise")


class Abstract(db.Model):
    __tablename__ = "abstracts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conferences.id"),
                              nullable=False, index=True)

    title = db.Column(db.String(400), nullable=False)
    authors = db.Column(db.Text, nullable=False)
    body = db.Column(db.Text, nullable=False)
    track = db.Column(db.String(120), default="")
    presentation_type = db.Column(db.String(40), default="Either")
    keywords = db.Column(db.String(300), default="")
    coi = db.Column(db.Text, default="")

    figure_filename = db.Column(db.String(240))
    status = db.Column(db.String(40), default="submitted", nullable=False)
    reviewer_notes = db.Column(db.Text, default="")
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)

    conference = db.relationship("Conference")
    author = db.relationship("User", foreign_keys=[user_id], backref="abstracts")
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])
