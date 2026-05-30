"""Seeding from the wizard form / placeholder.yaml.

Both code paths converge here: the wizard collects form data, augments it
with the placeholder.yaml defaults for any field the admin didn't change,
and then this module writes everything to the DB in one transaction.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ..extensions import db
from ..models import (
    Announcement, CommitteeMember, Conference, FooterColumn, FooterLink,
    NavItem, Page, User, get_site_settings,
)
from ..models.conference import PriceTier
from ..models.user import ensure_roles_exist
from .slugs import slugify


log = logging.getLogger(__name__)


def load_placeholder_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        log.warning("placeholder.yaml not found at %s", p)
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Seed from a merged dict (wizard form + yaml fallbacks).
# ---------------------------------------------------------------------------

def seed_initial(data: dict[str, Any]) -> User:
    """Idempotent-ish: writes every section from `data` into the DB.

    Returns the admin User row.
    """
    ensure_roles_exist()

    site = data.get("site") or {}
    admin = data.get("admin") or {}
    palette = data.get("palette") or {}
    fonts = data.get("fonts") or {}

    # --- Site settings -----------------------------------------------------
    s = get_site_settings()
    for k in ("name", "tagline", "short_name", "browser_tab_title",
              "copyright_line", "contact_email", "locale", "timezone",
              "currency_code", "currency_symbol"):
        v = site.get(k)
        if v is not None:
            db_key = {"name": "site_name"}.get(k, k)
            setattr(s, db_key, str(v))
    for k, v in palette.items():
        attr = f"palette_{k}"
        if hasattr(s, attr):
            setattr(s, attr, str(v))
    for k in ("heading", "body", "link", "ui"):
        v = fonts.get(k)
        if v:
            setattr(s, f"font_{k}", str(v))

    # --- Admin user --------------------------------------------------------
    admin_email = (admin.get("email") or "").strip().lower()
    if not admin_email:
        raise ValueError("Admin email is required.")
    u = User.query.filter_by(email=admin_email).first()
    if not u:
        u = User(email=admin_email)
        db.session.add(u)
    u.role_name = "admin"
    u.full_name = (admin.get("full_name") or u.full_name or "").strip() or None

    # --- Navigation --------------------------------------------------------
    if data.get("navigation"):
        NavItem.query.delete()
        for i, item in enumerate(data["navigation"], start=1):
            db.session.add(NavItem(
                label=(item.get("label") or "").strip() or "Link",
                target=(item.get("target") or "home").strip(),
                display_order=i * 10,
                visible=True,
            ))

    # --- Footer ------------------------------------------------------------
    footer = data.get("footer") or {}
    cols = footer.get("columns") or []
    if cols:
        FooterColumn.query.delete()
        for i, col in enumerate(cols, start=1):
            c = FooterColumn(
                title=(col.get("title") or "").strip() or "Column",
                display_order=i * 10,
            )
            db.session.add(c)
            db.session.flush()
            for j, ln in enumerate(col.get("links") or [], start=1):
                db.session.add(FooterLink(
                    column_id=c.id,
                    label=(ln.get("label") or "").strip() or "Link",
                    target=(ln.get("target") or "home").strip(),
                    display_order=j * 10,
                ))

    # --- Pages -------------------------------------------------------------
    for p in data.get("pages") or []:
        slug = slugify(p.get("slug") or p.get("title") or "page")
        existing = Page.query.filter_by(slug=slug).first()
        if existing:
            continue  # don't clobber re-runs
        db.session.add(Page(
            slug=slug,
            title=(p.get("title") or "").strip() or slug.title(),
            body=p.get("body") or "",
            published=True,
        ))

    # --- Committee ---------------------------------------------------------
    for i, c in enumerate(data.get("committee") or [], start=1):
        if not (c.get("full_name") or "").strip():
            continue
        db.session.add(CommitteeMember(
            title=(c.get("title") or "").strip(),
            full_name=c["full_name"].strip(),
            role=(c.get("role") or "").strip(),
            affiliation=(c.get("affiliation") or "").strip(),
            position=(c.get("position") or "").strip(),
            interests=(c.get("interests") or "").strip(),
            orcid=(c.get("orcid") or "").strip(),
            scholar_url=(c.get("scholar_url") or "").strip(),
            website_url=(c.get("website_url") or "").strip(),
            display_order=int(c.get("display_order") or i * 10),
        ))

    # --- Announcements -----------------------------------------------------
    for a in data.get("announcements") or []:
        if not (a.get("title") or "").strip():
            continue
        db.session.add(Announcement(
            title=a["title"].strip(),
            kind=(a.get("kind") or "News").strip(),
            body=a.get("body") or "",
            pinned=bool(a.get("pinned")),
        ))

    # --- Conferences -------------------------------------------------------
    for cf in data.get("conferences") or []:
        slug = slugify(cf.get("slug") or cf.get("title") or "")
        if not slug:
            continue
        if Conference.query.filter_by(slug=slug).first():
            continue
        try:
            start = date.fromisoformat(str(cf["start_date"]))
            end = date.fromisoformat(str(cf["end_date"]))
        except (KeyError, ValueError):
            continue
        c = Conference(
            slug=slug,
            title=(cf.get("title") or slug.title()).strip(),
            subtitle=(cf.get("subtitle") or "").strip(),
            summary=cf.get("summary") or "",
            body=cf.get("body") or "",
            start_date=start, end_date=end,
            city=(cf.get("city") or "").strip(),
            venue=(cf.get("venue") or "").strip(),
            is_featured=bool(cf.get("is_featured")),
            abstract_deadline=_maybe_date(cf.get("abstract_deadline")),
            early_bird_deadline=_maybe_date(cf.get("early_bird_deadline")),
            tracks="\n".join(cf.get("tracks") or []),
        )
        db.session.add(c)
        db.session.flush()
        for i, t in enumerate(cf.get("price_tiers") or [], start=1):
            db.session.add(PriceTier(
                conference_id=c.id,
                name=(t.get("name") or f"Tier {i}").strip(),
                amount=int(t.get("amount") or 0),
                display_order=i * 10,
            ))

    db.session.commit()
    return u


def _maybe_date(v):
    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None
