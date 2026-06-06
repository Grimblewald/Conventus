"""Committee member profiles — name, role, portrait, links, display order."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class CommitteeMember(db.Model):
    __tablename__ = "committee_members"

    id = db.Column(db.Integer, primary_key=True)

    # If linked to a user account, the user can edit their own profile
    # provided `committee.edit_self` is granted to their role.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    # Identity
    title = db.Column(db.String(40), default="")          # Dr / Prof / Mx / ...
    full_name = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(120), default="")          # President / Secretary
    affiliation = db.Column(db.String(200), default="")
    dynamic_roles = db.Column(db.JSON, default=None)
    position = db.Column(db.String(200), default="")      # Senior Lecturer / ...
    interests = db.Column(db.Text, default="")            # free text / keywords

    # Links
    orcid = db.Column(db.String(40), default="")
    scholar_url = db.Column(db.String(400), default="")
    website_url = db.Column(db.String(400), default="")

    # Portrait
    portrait_filename = db.Column(db.String(255))         # square crop
    portrait_alt_text = db.Column(db.String(255), default="")

    # Order on /committee
    display_order = db.Column(db.Integer, default=100, nullable=False, index=True)
    is_contactable = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)

    @classmethod
    def visible_in_order(cls) -> list["CommitteeMember"]:
        return (
            cls.query.filter(cls.deleted_at.is_(None))
            .order_by(cls.display_order.asc(), cls.full_name.asc())
            .all()
        )

    @property
    def display_name(self) -> str:
        return f"{self.title} {self.full_name}".strip()
