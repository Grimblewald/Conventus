"""Past board — archive snapshots of previous committee terms."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class PastBoard(db.Model):
    __tablename__ = "past_boards"

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(120), nullable=False)
    term_start = db.Column(db.Date, nullable=True)
    term_end = db.Column(db.Date, nullable=True)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    members = db.relationship(
        "PastBoardMember",
        backref="board",
        lazy="joined",
        cascade="all, delete-orphan",
        order_by="PastBoardMember.display_order",
    )

    @property
    def label_default(self) -> str:
        """Generate a label from term_start if no label was set."""
        if self.term_start:
            return str(self.term_start.year)
        return self.label


class PastBoardMember(db.Model):
    __tablename__ = "past_board_members"

    id = db.Column(db.Integer, primary_key=True)
    past_board_id = db.Column(
        db.Integer,
        db.ForeignKey("past_boards.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    full_name = db.Column(db.String(200), nullable=False)
    title = db.Column(db.String(40), default="")
    role = db.Column(db.String(120), default="")
    affiliation = db.Column(db.String(200), default="")
    position = db.Column(db.String(200), default="")
    interests = db.Column(db.Text, default="")
    orcid = db.Column(db.String(40), default="")
    scholar_url = db.Column(db.String(400), default="")
    website_url = db.Column(db.String(400), default="")
    portrait_filename = db.Column(db.String(255))
    portrait_alt_text = db.Column(db.String(255), default="")
    display_order = db.Column(db.Integer, default=0, nullable=False)

    @property
    def display_name(self) -> str:
        return f"{self.title} {self.full_name}".strip()
