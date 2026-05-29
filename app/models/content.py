"""Site-content models: SiteSettings (palette/fonts/identity/images), Page,
NavItem, FooterColumn, FooterLink.

These are the things an admin (or a permitted committee member) edits from
the Site/Pages/Navigation/Footer admin panels. The shape is deliberately
boring — strings everywhere — so future migrations are cheap.
"""
from __future__ import annotations

from datetime import datetime

from ..extensions import db


# ---------------------------------------------------------------------------
# Site settings — single row, id=1.
# ---------------------------------------------------------------------------

class SiteSettings(db.Model):
    __tablename__ = "site_settings"

    id = db.Column(db.Integer, primary_key=True)

    # --- Identity ----------------------------------------------------------
    site_name = db.Column(db.String(120), default="Your Society", nullable=False)
    tagline = db.Column(db.String(240), default="")
    intro_paragraph = db.Column(db.Text, default="")
    intro_secondary = db.Column(db.Text, default="")
    short_name = db.Column(db.String(60), default="Society")
    browser_tab_title = db.Column(db.String(120), default="Your Society")
    copyright_line = db.Column(db.String(240), default="© {year} Your Society")
    contact_email = db.Column(db.String(200), default="")
    locale = db.Column(db.String(8), default="en")
    timezone = db.Column(db.String(40), default="UTC")
    currency_code = db.Column(db.String(8), default="USD")
    currency_symbol = db.Column(db.String(4), default="$")

    # --- Palette (every value an admin can change from Site → Palette) -----
    # Stored as hex/CSS colour strings.
    palette_page_bg = db.Column(db.String(24), default="#f7f5f1")
    palette_page_text = db.Column(db.String(24), default="#171717")
    palette_muted_text = db.Column(db.String(24), default="#5b5b5b")
    palette_link = db.Column(db.String(24), default="#2a4d8f")
    palette_link_hover = db.Column(db.String(24), default="#1a3669")
    palette_accent = db.Column(db.String(24), default="#2a4d8f")
    palette_accent_ink = db.Column(db.String(24), default="#ffffff")
    palette_header_bg = db.Column(db.String(24), default="#ffffff")
    palette_header_text = db.Column(db.String(24), default="#171717")
    palette_footer_bg = db.Column(db.String(24), default="#171717")
    palette_footer_text = db.Column(db.String(24), default="#dcdcdc")
    palette_card_bg = db.Column(db.String(24), default="#ffffff")
    palette_card_border = db.Column(db.String(24), default="#e4e0d6")
    palette_button_bg = db.Column(db.String(24), default="#171717")
    palette_button_text = db.Column(db.String(24), default="#ffffff")

    # --- Fonts (web-safe stack keys defined in services/fonts.py) ----------
    font_heading = db.Column(db.String(40), default="modern_serif")
    font_body = db.Column(db.String(40), default="system_sans")
    font_link = db.Column(db.String(40), default="system_sans")
    font_ui = db.Column(db.String(40), default="system_sans")

    # --- Images ------------------------------------------------------------
    hero_image_filename = db.Column(db.String(255))
    logo_filename = db.Column(db.String(255))
    favicon_filename = db.Column(db.String(255))
    og_image_filename = db.Column(db.String(255))

    # --- Misc --------------------------------------------------------------
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    # ------------------------------------------------------------------
    @property
    def palette(self) -> dict[str, str]:
        return {
            "page_bg": self.palette_page_bg,
            "page_text": self.palette_page_text,
            "muted_text": self.palette_muted_text,
            "link": self.palette_link,
            "link_hover": self.palette_link_hover,
            "accent": self.palette_accent,
            "accent_ink": self.palette_accent_ink,
            "header_bg": self.palette_header_bg,
            "header_text": self.palette_header_text,
            "footer_bg": self.palette_footer_bg,
            "footer_text": self.palette_footer_text,
            "card_bg": self.palette_card_bg,
            "card_border": self.palette_card_border,
            "button_bg": self.palette_button_bg,
            "button_text": self.palette_button_text,
        }


def get_site_settings() -> "SiteSettings":
    """Singleton getter. Creates the row if missing (idempotent)."""
    s = db.session.get(SiteSettings, 1)
    if not s:
        s = SiteSettings(id=1)
        db.session.add(s)
        db.session.commit()
    return s


# ---------------------------------------------------------------------------
# Pages — Markdown-bodied content under a slug. About / Privacy / Terms /
# Code of Conduct ship as seeded defaults; admins can add as many more.
# ---------------------------------------------------------------------------

class Page(db.Model):
    __tablename__ = "pages"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    title = db.Column(db.String(240), nullable=False)
    body = db.Column(db.Text, default="")
    published = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)


# ---------------------------------------------------------------------------
# Navigation — top-level menu items in the header.
# ---------------------------------------------------------------------------

class NavItem(db.Model):
    __tablename__ = "nav_items"

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(80), nullable=False)
    # Targets follow the same scheme as placeholder.yaml:
    #   "home", "conferences", "committee", "contact"  (built-ins)
    #   "page:<slug>"                                  (custom page)
    #   "url:https://..."                              (external link)
    target = db.Column(db.String(255), nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    visible = db.Column(db.Boolean, default=True, nullable=False)
    open_in_new_tab = db.Column(db.Boolean, default=False, nullable=False)

    @classmethod
    def visible_in_order(cls) -> list["NavItem"]:
        return (
            cls.query.filter_by(visible=True)
            .order_by(cls.display_order.asc(), cls.id.asc())
            .all()
        )


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

class FooterColumn(db.Model):
    __tablename__ = "footer_columns"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(80), nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)

    links = db.relationship(
        "FooterLink",
        backref="column",
        lazy="joined",
        cascade="all, delete-orphan",
        order_by="FooterLink.display_order",
    )

    @classmethod
    def visible_in_order(cls) -> list["FooterColumn"]:
        return cls.query.order_by(cls.display_order.asc(), cls.id.asc()).all()


class FooterLink(db.Model):
    __tablename__ = "footer_links"

    id = db.Column(db.Integer, primary_key=True)
    column_id = db.Column(
        db.Integer,
        db.ForeignKey("footer_columns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    label = db.Column(db.String(80), nullable=False)
    target = db.Column(db.String(255), nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    open_in_new_tab = db.Column(db.Boolean, default=False, nullable=False)
