"""Per-conference organising committee members."""
from __future__ import annotations

from ..extensions import db


class OrganisingCommitteeMember(db.Model):
    __tablename__ = "organising_committee_members"

    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(
        db.Integer,
        db.ForeignKey("conferences.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    full_name = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(120), default="")
    affiliation = db.Column(db.String(200), default="")
    email = db.Column(db.String(200), default="")
    portrait_filename = db.Column(db.String(255))
    display_order = db.Column(db.Integer, default=0, nullable=False)

    conference = db.relationship("Conference", backref=db.backref(
        "organising_committee", lazy="joined", cascade="all, delete-orphan",
        order_by="OrganisingCommitteeMember.display_order",
    ))
