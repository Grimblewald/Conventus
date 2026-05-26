"""Announcements posted on the home page."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


ANN_KINDS = ("News", "Call", "Award", "Event")


class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
    kind = db.Column(db.String(40), default="News", nullable=False)
    body = db.Column(db.Text, default="")
    published_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    pinned = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)
