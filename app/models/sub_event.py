"""Per-conference sub-events (workshops, dinners, excursions)."""
from __future__ import annotations

from ..extensions import db


class SubEvent(db.Model):
    __tablename__ = "sub_events"

    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(
        db.Integer,
        db.ForeignKey("conferences.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    price = db.Column(db.Integer, default=0, nullable=False)
    eligibility_note = db.Column(db.Text, default="")
    preference_schema = db.Column(db.JSON, default=None)
    display_order = db.Column(db.Integer, default=0, nullable=False)

    conference = db.relationship("Conference", backref=db.backref(
        "sub_events", lazy="joined", cascade="all, delete-orphan",
        order_by="SubEvent.display_order",
    ))
