"""Site-content models: SiteSettings (palette/fonts/identity/images), Page,
NavItem, FooterColumn, FooterLink.

These are the things an admin (or a permitted committee member) edits from
the Site/Pages/Navigation/Footer admin panels. The shape is deliberately
boring — strings everywhere — so future migrations are cheap.
"""
from __future__ import annotations

from datetime import datetime

from cryptography.fernet import Fernet

from ..extensions import db


# ---------------------------------------------------------------------------
# Site settings — single row, id=1.
# ---------------------------------------------------------------------------

class SiteSettings(db.Model):
    __tablename__ = "site_settings"

    id = db.Column(db.Integer, primary_key=True)

    # --- Identity ----------------------------------------------------------
    site_name = db.Column(db.String(120), default="Name Your Society", nullable=False)
    tagline = db.Column(db.String(240), default="")
    intro_paragraph = db.Column(db.Text, default="")
    closing_statement = db.Column(db.Text, default="")
    short_name = db.Column(db.String(60), default="Society")
    browser_tab_title = db.Column(db.String(120), default="Name Your Society")
    copyright_line = db.Column(db.String(240), default="© {year} Name Your Society")
    contact_email = db.Column(db.String(200), default="")
    locale = db.Column(db.String(8), default="en")
    timezone = db.Column(db.String(40), default="UTC")
    currency_code = db.Column(db.String(8), default="USD")
    currency_symbol = db.Column(db.String(4), default="$")

    # --- Payments ---------------------------------------------------------
    payment_portal_enabled = db.Column(db.Boolean, default=False, nullable=False)

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
    logo_height_px = db.Column(db.Integer, nullable=True)
    favicon_filename = db.Column(db.String(255))
    og_image_filename = db.Column(db.String(255))

    # --- Board / committee terms -------------------------------------------
    board_term_start = db.Column(db.Date, nullable=True)
    board_term_interval_months = db.Column(db.Integer, nullable=True)
    board_last_archived_at = db.Column(db.DateTime, nullable=True)

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
# Payment gateway configuration
# ---------------------------------------------------------------------------

class PaymentGatewayConfig(db.Model):
    __tablename__ = "payment_gateway_config"

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(40), unique=True, nullable=False)
    is_enabled = db.Column(db.Boolean, default=False, nullable=False)
    is_test_mode = db.Column(db.Boolean, default=True, nullable=False)
    merchant_id = db.Column(db.String(120), default="")
    api_key_id = db.Column(db.String(120), default="")
    api_secret_encrypted = db.Column(db.Text, default="")
    webhooks_key_id = db.Column(db.String(120), default="")
    webhooks_secret_encrypted = db.Column(db.Text, default="")
    api_key_set_at = db.Column(db.DateTime, nullable=True)
    expiry_warnings_sent = db.Column(db.JSON, default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    def get_api_secret(self) -> str:
        if not self.api_secret_encrypted:
            return ""
        try:
            from flask import current_app
            key = _fernet_key()
            f = Fernet(key)
            return f.decrypt(self.api_secret_encrypted.encode()).decode()
        except Exception:
            return ""

    def set_api_secret(self, secret: str) -> None:
        if not secret:
            self.api_secret_encrypted = ""
            return
        f = Fernet(_fernet_key())
        self.api_secret_encrypted = f.encrypt(secret.encode()).decode()

    def get_webhooks_secret(self) -> str:
        if not self.webhooks_secret_encrypted:
            return ""
        try:
            f = Fernet(_fernet_key())
            return f.decrypt(self.webhooks_secret_encrypted.encode()).decode()
        except Exception:
            return ""

    def set_webhooks_secret(self, secret: str) -> None:
        if not secret:
            self.webhooks_secret_encrypted = ""
            return
        f = Fernet(_fernet_key())
        self.webhooks_secret_encrypted = f.encrypt(secret.encode()).decode()


def _fernet_key() -> bytes:
    # Derived from SECRET_KEY: rotating SECRET_KEY invalidates every stored
    # gateway secret (decryption then returns "" and the gateway reports
    # itself unconfigured until credentials are re-entered).
    import hashlib
    import base64
    from flask import current_app
    raw = current_app.config.get("SECRET_KEY", "fallback-key")
    h = hashlib.sha256(raw.encode()).digest()
    return base64.urlsafe_b64encode(h)


def get_payment_gateway_config(provider: str) -> PaymentGatewayConfig | None:
    return (
        PaymentGatewayConfig.query
        .filter_by(provider=provider)
        .first()
    )


def get_active_payment_gateway() -> PaymentGatewayConfig | None:
    return (
        PaymentGatewayConfig.query
        .filter_by(is_enabled=True)
        .first()
    )


# ---------------------------------------------------------------------------
# Document template — one row per kind (invoice / receipt / adjustment).
# Consolidates the email cover, the PDF body, and the business/tax details.
# The pdf_body column is not consumed yet (the renderer lands in a later step).
# ---------------------------------------------------------------------------

class DocumentTemplate(db.Model):
    __tablename__ = "document_template"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), unique=True, index=True, nullable=False)

    # --- Email cover -------------------------------------------------------
    subject = db.Column(db.String(200), default="")
    email_body = db.Column(db.Text, default="")
    from_name = db.Column(db.String(120), default="")
    from_email = db.Column(db.String(200), default="")
    footer_text = db.Column(db.String(400), default="")

    # --- PDF document body (inserted into a curated LaTeX skeleton later) --
    pdf_body = db.Column(db.Text, default="")

    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    @property
    def content_hash(self) -> str:
        # Hash of everything that affects the rendered PDF, for cache keying
        # (plan §5). The shared FinancialIdentity feeds every kind's render,
        # so its fields are part of the key — editing the identity re-keys
        # (and re-warms) all kinds. Computed on demand — not stored.
        import hashlib
        ident = get_financial_identity()
        raw = "|".join([
            self.kind or "",
            self.pdf_body or "",
            ident.render_fingerprint,
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Financial identity — the single source of truth for who issues financial
# documents: legal entity, ABN, GST status, address, payment details,
# signatory. Every document kind draws from this one row; nothing
# business-identity-shaped lives on the per-kind templates.
# ---------------------------------------------------------------------------

class FinancialIdentity(db.Model):
    __tablename__ = "financial_identity"

    id = db.Column(db.Integer, primary_key=True)
    legal_name = db.Column(db.String(200), default="")   # falls back to site name
    abn = db.Column(db.String(40), default="")
    gst_registered = db.Column(db.Boolean, default=False, nullable=False)
    # Percent, not a fraction. Nullable so the guarded migration can add it to
    # existing rows without inventing a rate — NULL reads as the Australian
    # 10% through gst_percent below.
    gst_rate = db.Column(db.Float, nullable=True)
    address = db.Column(db.Text, default="")             # multi-line, incl. C/- line
    contact_email = db.Column(db.String(200), default="")
    payment_instructions = db.Column(db.Text, default="")  # EFT block on invoices
    signatory_name = db.Column(db.String(120), default="")
    signatory_role = db.Column(db.String(120), default="")
    # Fixed-name assets under var/financial-assets (never web-served — a
    # signature image must not be publicly reachable).
    logo_filename = db.Column(db.String(80), default="")
    signature_filename = db.Column(db.String(80), default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    DEFAULT_GST_RATE = 10.0

    @property
    def gst_percent(self) -> float:
        """The GST rate as a percentage — 10 for Australia, the default.

        Prices are quoted GST-inclusive, so the tax component of a total is
        `total × rate / (100 + rate)`; at 10% that is the familiar total ÷ 11,
        which is a 10% rate expressed against an inclusive amount, not an 11%
        one.
        """
        if self.gst_rate is None:
            return self.DEFAULT_GST_RATE
        return float(self.gst_rate)

    @property
    def gst_percent_label(self) -> str:
        """The rate as it should be printed: "10", not "10.0"; "12.5" kept."""
        return f"{self.gst_percent:g}"

    @property
    def render_fingerprint(self) -> str:
        """The identity facts that change a rendered document, joined for
        cache keying (see DocumentTemplate.content_hash)."""
        return "|".join([
            self.legal_name or "", self.abn or "",
            "1" if self.gst_registered else "0",
            self.gst_percent_label,
            self.address or "", self.contact_email or "",
            self.payment_instructions or "",
            self.signatory_name or "", self.signatory_role or "",
            self._asset_stamp(self.logo_filename),
            self._asset_stamp(self.signature_filename),
        ])

    @staticmethod
    def _asset_stamp(filename: str | None) -> str:
        """`<name>:<size>:<mtime_ns>` for an asset file, or just the name.

        The filename alone is NOT a usable cache key: assets are stored at
        fixed paths (logo.png, signature.png), so *replacing* an image leaves
        the name identical and every cached document would keep rendering the
        old picture. Stat rather than a content hash — it changes on every
        write, and a 2MB read on each cache-key computation is not worth the
        difference.
        """
        if not filename:
            return ""
        try:
            from ..services.documents import financial_assets_dir
            st = (financial_assets_dir() / filename).stat()
            return f"{filename}:{st.st_size}:{st.st_mtime_ns}"
        except (OSError, RuntimeError, ImportError):
            # No file, or no app context (tooling) — the name still keys the
            # difference between "an asset is set" and "none is".
            return filename


def get_financial_identity() -> FinancialIdentity:
    """The single FinancialIdentity row, lazily created."""
    ident = FinancialIdentity.query.first()
    if not ident:
        ident = FinancialIdentity()
        db.session.add(ident)
        db.session.commit()
    return ident


# Per-kind seed wording. Every kind draws from the same variable vocabulary
# ({invoice_type}, {gst_amount}, {amount_ex_gst}, …) so any kind renders
# through the one document renderer. The cover email is deliberately short:
# the formal document is the attached PDF.
_DOCUMENT_DEFAULTS = {
    # An invoice REQUESTS payment. It must never read like a receipt.
    "invoice": {
        "subject": "Invoice {transaction_id} — {conference_title}",
        "email_body": (
            "Dear {user_name},\n\n"
            "Please find attached invoice {transaction_id} for "
            "{conference_title}.\n\n"
            "Item: {tier_name}\n"
            "Amount due: {currency_symbol}{amount} {currency_code}\n"
            "Due date: {due_date}\n\n"
            "Pay online:\n{payment_link}\n\n"
            "Or by bank transfer:\n{payment_instructions}\n\n"
            "Please quote {transaction_id} with your payment.\n\n"
            "{business_legal_name}"
        ),
        "footer_text": "Thank you from {site_name}",
    },
    # A receipt CONFIRMS payment already made.
    "receipt": {
        "subject": "Receipt {transaction_id} — {conference_title}",
        "email_body": (
            "Dear {user_name},\n\n"
            "Thank you — your payment has been received. Your receipt is "
            "attached.\n\n"
            "Item: {tier_name}\n"
            "Amount paid: {currency_symbol}{amount} {currency_code}\n"
            "Date: {payment_date}\n"
            "Reference: {transaction_id}\n\n"
            "{business_legal_name}"
        ),
        "footer_text": "Thank you from {site_name}",
    },
    # An adjustment note records a refund or correction against a payment.
    "adjustment": {
        "subject": "Adjustment note {transaction_id} — {conference_title}",
        "email_body": (
            "Dear {user_name},\n\n"
            "An adjustment has been made to your payment for "
            "{conference_title}. The adjustment note is attached.\n\n"
            "Item: {tier_name}\n"
            "Amount: {currency_symbol}{amount} {currency_code}\n"
            "Date: {payment_date}\n"
            "Reference: {transaction_id}\n\n"
            "Any refund is returned to the original payment method and can "
            "take a few business days to appear.\n\n"
            "{business_legal_name}"
        ),
        "footer_text": "Thank you from {site_name}",
    },
}


def get_document_template(kind: str) -> DocumentTemplate:
    """Lazy-seed getter, keyed by kind. First access for a kind creates the
    row from `_DOCUMENT_DEFAULTS[kind]`."""
    t = DocumentTemplate.query.filter_by(kind=kind).first()
    if not t:
        t = DocumentTemplate(kind=kind, **_DOCUMENT_DEFAULTS[kind])
        db.session.add(t)
        db.session.commit()
    return t


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
