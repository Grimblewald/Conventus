"""Saved form schemas reusable across conferences."""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


class FormTemplate(db.Model):
    __tablename__ = "form_templates"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    form_type = db.Column(db.String(20), nullable=False)
    schema = db.Column(db.JSON, nullable=False)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                              nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    created_by = db.relationship("User")
