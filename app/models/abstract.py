"""Abstract submissions (1 optional figure each)."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db

SPEAKER_STATUSES = ("plenary", "keynote", "invited", "accepted")
SPEAKER_STATUS_ORDER = {s: i for i, s in enumerate(SPEAKER_STATUSES)}
ALL_STATUSES = ("submitted", "accepted", "rejected", "revise",
                "plenary", "keynote", "invited")


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
    custom_data = db.Column(db.JSON, default=None)

    figure_filename = db.Column(db.String(240))
    profile_picture_filename = db.Column(db.String(240))
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

    @property
    def presenting_author(self) -> tuple[str, str]:
        if not self.authors or not self.authors.strip():
            return ("", "")
        first_line = self.authors.strip().split("\n")[0].strip()
        parts = first_line.split("|")
        name = parts[0].strip() if parts else ""
        affil = parts[2].strip() if len(parts) > 2 else ""
        return (name, affil)

    @property
    def is_speaker(self) -> bool:
        return self.status in SPEAKER_STATUSES

    @property
    def speaker_sort_key(self) -> int:
        return SPEAKER_STATUS_ORDER.get(self.status, len(SPEAKER_STATUSES))
