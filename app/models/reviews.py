"""Review system: peer review assignments, scoring, and recommendations."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class ConferenceReviewer(db.Model):
    """Links a user as a reviewer for a specific conference."""
    __tablename__ = "conference_reviewers"

    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conferences.id"),
                              nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False, index=True)
    expertise = db.Column(db.Text, default="")
    max_reviews = db.Column(db.Integer, default=5, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    conference = db.relationship("Conference", back_populates="reviewers")
    user = db.relationship("User", backref="conference_reviewer_entries")

    __table_args__ = (
        db.UniqueConstraint("conference_id", "user_id",
                            name="uq_conference_reviewer"),
    )


class ReviewAssignment(db.Model):
    """One reviewer's review of one abstract."""
    __tablename__ = "review_assignments"

    id = db.Column(db.Integer, primary_key=True)
    abstract_id = db.Column(db.Integer, db.ForeignKey("abstracts.id"),
                            nullable=False, index=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                            nullable=False, index=True)

    score = db.Column(db.Integer, nullable=True)
    recommendation = db.Column(db.String(20), nullable=True)
    comments_author = db.Column(db.Text, default="")
    comments_chair = db.Column(db.Text, default="")
    decline_reason = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="pending", nullable=False)

    claimed_at = db.Column(db.DateTime, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    abstract = db.relationship("Abstract", back_populates="reviews")
    reviewer = db.relationship("User", backref="review_assignments")

    __table_args__ = (
        db.UniqueConstraint("abstract_id", "reviewer_id",
                            name="uq_review_assignment"),
    )
