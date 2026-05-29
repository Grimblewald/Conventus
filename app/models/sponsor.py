"""Sponsor tiers and sponsors — per-conference, logo + link."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class SponsorTier(db.Model):
    __tablename__ = "sponsor_tiers"

    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(
        db.Integer,
        db.ForeignKey("conferences.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name = db.Column(db.String(80), nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)

    sponsors = db.relationship(
        "Sponsor",
        backref="tier",
        lazy="joined",
        cascade="all, delete-orphan",
        order_by="Sponsor.display_order",
    )


class Sponsor(db.Model):
    __tablename__ = "sponsors"

    id = db.Column(db.Integer, primary_key=True)
    tier_id = db.Column(
        db.Integer,
        db.ForeignKey("sponsor_tiers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500))
    logo_filename = db.Column(db.String(255))
    display_order = db.Column(db.Integer, default=0, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
