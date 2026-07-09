"""Abstract submissions (1 optional figure each)."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db

SPEAKER_STATUSES = ("plenary", "keynote", "invited", "accepted")
SPEAKER_STATUS_ORDER = {s: i for i, s in enumerate(SPEAKER_STATUSES)}
ALL_STATUSES = ("draft", "submitted", "accepted", "rejected", "revise",
                "plenary", "keynote", "invited")


class Abstract(db.Model):
    __tablename__ = "abstracts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conferences.id"),
                              nullable=False, index=True)
    registration_id = db.Column(db.Integer, db.ForeignKey("registrations.id"),
                                nullable=True, index=True)

    title = db.Column(db.String(400), nullable=False)
    authors = db.Column(db.Text, nullable=False)
    body = db.Column(db.Text, nullable=False)
    track = db.Column(db.String(120), default="")
    presentation_type = db.Column(db.String(40), default="Either")
    keywords = db.Column(db.String(300), default="")
    coi = db.Column(db.Text, default="")
    custom_data = db.Column(db.JSON, default=None)
    presenting_author_index = db.Column(db.Integer, default=0)
    references = db.Column(db.JSON, default=None)

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
    registration = db.relationship("Registration", foreign_keys=[registration_id])
    reviews = db.relationship("ReviewAssignment", lazy="selectin",
                              back_populates="abstract")

    @property
    def review_scores(self) -> list[int]:
        """Submitted review scores, excluding pending/draft reviews."""
        return [r.score for r in self.reviews
                if r.status == "completed" and r.score is not None]

    @property
    def mean_score(self) -> float | None:
        scores = self.review_scores
        if not scores:
            return None
        return round(sum(scores) / len(scores), 1)

    @property
    def recommendation_tally(self) -> dict[str, int]:
        """Count of completed review recommendations."""
        from collections import Counter
        recs = [r.recommendation for r in self.reviews
                if r.status == "completed" and r.recommendation]
        return dict(Counter(recs))

    @property
    def presenting_author(self) -> tuple[str, str]:
        if not self.authors or not self.authors.strip():
            return ("", "")
        lines = self.authors.strip().split("\n")
        idx = max(0, min(self.presenting_author_index or 0, len(lines) - 1))
        parts = lines[idx].split("|")
        name = parts[0].strip() if parts else ""
        affil = parts[2].strip() if len(parts) > 2 else ""
        return (name, affil)

    @property
    def is_speaker(self) -> bool:
        return self.status in SPEAKER_STATUSES

    @property
    def speaker_sort_key(self) -> int:
        return SPEAKER_STATUS_ORDER.get(self.status, len(SPEAKER_STATUSES))
