"""Conferences + their price tiers."""
from __future__ import annotations

from datetime import date, datetime

from ..extensions import db


class Conference(db.Model):
    __tablename__ = "conferences"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    title = db.Column(db.String(240), nullable=False)
    subtitle = db.Column(db.String(400), default="")
    summary = db.Column(db.Text, default="")
    body = db.Column(db.Text, default="")            # Markdown
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    city = db.Column(db.String(120), default="")
    venue = db.Column(db.String(200), default="")
    hero_caption = db.Column(db.String(200), default="")
    hero_image_filename = db.Column(db.String(255))
    booklet_filename = db.Column(db.String(255))
    is_featured = db.Column(db.Boolean, default=False, nullable=False)
    is_draft = db.Column(db.Boolean, default=False, nullable=False)

    abstract_deadline = db.Column(db.Date)
    early_bird_deadline = db.Column(db.Date)
    registration_deadline = db.Column(db.Date)

    is_accepting_abstracts = db.Column(db.Boolean, default=True, nullable=False)
    is_accepting_registrations = db.Column(db.Boolean, default=True, nullable=False)
    abstracts_reopen_date = db.Column(db.Date)
    registrations_reopen_date = db.Column(db.Date)

    external_registration_url = db.Column(db.String(500))
    external_abstract_url = db.Column(db.String(500))

    tracks = db.Column(db.Text, default="")          # newline-separated
    committee = db.Column(db.Text, default="")       # "Name, Institution\n..."

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)

    price_tiers = db.relationship(
        "PriceTier",
        backref="conference",
        lazy="joined",
        cascade="all, delete-orphan",
        order_by="PriceTier.display_order",
    )
    sponsor_tiers = db.relationship(
        "SponsorTier",
        backref="conference",
        lazy="joined",
        cascade="all, delete-orphan",
        order_by="SponsorTier.display_order",
    )

    @property
    def date_range(self) -> str:
        s, e = self.start_date, self.end_date
        if s == e:
            return s.strftime("%-d %B %Y")
        if s.month == e.month and s.year == e.year:
            return f"{s.strftime('%-d')}\u2013{e.strftime('%-d %B %Y')}"
        if s.year == e.year:
            return f"{s.strftime('%-d %B')} \u2013 {e.strftime('%-d %B %Y')}"
        return f"{s.strftime('%-d %b %Y')} \u2013 {e.strftime('%-d %b %Y')}"

    @property
    def is_upcoming(self) -> bool:
        return self.end_date >= date.today()

    def tracks_list(self) -> list[str]:
        return [t.strip() for t in (self.tracks or "").split("\n") if t.strip()]

    def auto_reopen(self) -> bool:
        changed = False
        today = date.today()
        if (not self.is_accepting_abstracts and self.abstracts_reopen_date
                and self.abstracts_reopen_date <= today):
            self.is_accepting_abstracts = True
            changed = True
        if (not self.is_accepting_registrations and self.registrations_reopen_date
                and self.registrations_reopen_date <= today):
            self.is_accepting_registrations = True
            changed = True
        return changed

    @property
    def accepts_abstracts(self) -> bool:
        if not self.is_accepting_abstracts:
            return False
        if self.abstract_deadline and self.abstract_deadline < date.today():
            return False
        return True

    @property
    def accepts_registrations(self) -> bool:
        if not self.is_accepting_registrations:
            return False
        if self.registration_deadline and self.registration_deadline < date.today():
            return False
        return True


class PriceTier(db.Model):
    """Per-conference, per-tier price. Currency comes from SiteSettings."""
    __tablename__ = "price_tiers"

    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(
        db.Integer,
        db.ForeignKey("conferences.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name = db.Column(db.String(80), nullable=False)        # Academic / Student / ...
    amount = db.Column(db.Integer, default=0, nullable=False)  # minor unit-agnostic
    description = db.Column(db.String(400), default="")
    display_order = db.Column(db.Integer, default=0, nullable=False)


# Late import — ensures SponsorTier is defined before SQLAlchemy resolves the
# string reference in Conference.sponsor_tiers relationship.
from .sponsor import Sponsor, SponsorTier  # noqa: E402, F401
