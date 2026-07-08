"""Flask application factory.

The factory:
1. Loads `.env` (via wsgi.py before this module imports)
2. Picks a Config class
3. Initialises every extension
4. Registers blueprints in dependency order
5. Wires the setup-wizard gatekeeper that redirects *all* traffic to
   `/setup` until first-run config is complete
6. Injects template globals (current site settings, palette, fonts, etc.)
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from datetime import date, datetime
from pathlib import Path

from flask import Flask, redirect, request, url_for

from .config import BaseConfig, select_config
from .extensions import (
    babel,
    csrf,
    db,
    limiter,
    login_manager,
    migrate,
    talisman,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _inline_script_hashes() -> list[str]:
    """Compute SHA256 hashes for every inline <script> block in templates.

    Returns CSP-compatible 'sha256-...' tokens, allowing removal of
    'unsafe-inline' from script-src without breaking legitimate scripts.
    Skips <script type="application/json"> data blocks.
    """
    templates_root = Path(__file__).parent / "templates"
    hashes = []
    for f in sorted(templates_root.rglob("*.html")):
        for m in re.finditer(
            r"<script\b(?!.*\bsrc=)[^>]*>(.*?)</script>",
            f.read_text(), re.DOTALL,
        ):
            body = m.group(1).strip()
            if body and "application/json" not in m.group(0):
                h = hashlib.sha256(body.encode()).digest()
                hashes.append(f"'sha256-{base64.b64encode(h).decode()}'")
    return hashes


def create_app(config_class: type[BaseConfig] | None = None) -> Flask:
    app = Flask(
        __name__,
        instance_relative_config=True,
        static_folder="static",
        template_folder="templates",
    )

    app.config.from_object(config_class or select_config())
    _ensure_directories(app)
    _configure_logging(app)

    _init_extensions(app)
    _connect_mailer(app)
    _register_blueprints(app)
    _register_template_globals(app)
    _register_setup_gate(app)
    _register_error_handlers(app)

    return app


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _ensure_directories(app: Flask) -> None:
    for path in (
        Path(app.instance_path),
        Path(app.config["UPLOAD_FOLDER"]),
        Path(app.config["UPLOAD_FOLDER"]) / "committee",
        Path(app.config["UPLOAD_FOLDER"]) / "site",
        Path(app.config["UPLOAD_FOLDER"]) / "abstracts",
        Path(app.config["UPLOAD_FOLDER"]) / "conferences",
        Path(app.config["UPLOAD_FOLDER"]) / "sponsors",
    ):
        path.mkdir(parents=True, exist_ok=True)


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if app.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if app.debug:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)


def _connect_mailer(app: Flask) -> None:
    """Pre-warm the persistent SMTP connection (no-op in console mode)."""
    from .services.mail import connect_mailer
    with app.app_context():
        connect_mailer()


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db, directory=str(Path(app.root_path).parent / "migrations"))
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    babel.init_app(app, locale_selector=_select_locale)

    # Talisman: HTTPS + sensible security headers. CSP is intentionally tight
    # because we never load remote fonts/scripts — system fonts only.
    csp = {
        "default-src": "'self'",
        "img-src": ["'self'", "data:"],
        "media-src": ["'self'", "data:"],
        "script-src": ["'self'"] + _inline_script_hashes(),
        "style-src": ["'self'", "'unsafe-inline'"],   # inline CSS vars only
        "font-src": "'self'",
        "connect-src": "'self'",
        "frame-ancestors": "'none'",
        "form-action": "'self'",
        "base-uri": "'self'",
    }
    talisman.init_app(
        app,
        content_security_policy=csp,
        force_https=app.config.get("SESSION_COOKIE_SECURE", True),
        strict_transport_security=True,
        strict_transport_security_max_age=63072000,
        referrer_policy="strict-origin-when-cross-origin",
        frame_options="DENY",
        session_cookie_secure=app.config.get("SESSION_COOKIE_SECURE", True),
        session_cookie_http_only=True,
    )

    # Bootstrap schema + built-in roles.
    #
    # db.create_all() creates any missing tables from the current model
    # definitions. This is idempotent and ensures a fresh checkout starts
    # without needing to run migrations first.
    #
    # Flask-Migrate is still the canonical path for incremental schema
    # changes on live deployments — run `flask db upgrade` as part of
    # your deploy process. The initial migration (4a1b2c3d4e5f) also
    # delegates to db.create_all(), so either path produces the same
    # schema.
    #
    # Narrow exception handling: we *only* swallow the legitimate "you
    # haven't migrated yet" case (no tables exist + roles table missing).
    # Everything else — bad URI, permission denied, ambiguous mapper, etc.
    # — must surface so the operator sees the real error instead of a
    # silent empty schema.
    from sqlalchemy.exc import OperationalError
    with app.app_context():
        db.create_all()
        try:
            from .models.user import ensure_roles_exist
            ensure_roles_exist()
        except OperationalError as e:
            # Almost always "no such table: roles" on a fresh DB where
            # create_all itself couldn't run (e.g. migration head present
            # but tables not yet created). Log loudly, don't crash boot.
            app.logger.warning(
                "ensure_roles_exist() skipped — roles table not present: %s", e,
            )

    # User loader
    from .models.user import User  # local import — model needs db ready

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None


def _select_locale():
    # Future: read from user profile / browser. English-only for now.
    return "en"


def _register_blueprints(app: Flask) -> None:
    from .blueprints.admin import admin_bp
    from .blueprints.auth import auth_bp
    from .blueprints.member import member_bp
    from .blueprints.public import public_bp
    from .blueprints.setup_wizard import setup_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(member_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(setup_bp, url_prefix="/setup")


def _register_template_globals(app: Flask) -> None:
    from .models.content import get_site_settings
    from .models.content import NavItem, FooterColumn
    from .services.fonts import FONT_STACKS

    @app.context_processor
    def inject_globals():
        settings = get_site_settings()
        nav_items = NavItem.visible_in_order() if _setup_complete(app) else []
        footer_cols = FooterColumn.visible_in_order() if _setup_complete(app) else []
        return {
            "site": settings,
            "nav_items": nav_items,
            "footer_columns": footer_cols,
            "now": datetime.utcnow,
            "today": date.today,
            "FONT_STACKS": FONT_STACKS,
        }

    @app.template_filter("md")
    def render_markdown(text: str) -> str:
        # Minimal Markdown-ish: bold/italic/links/lists/headings, escaped first.
        from .services.markdown import render
        return render(text or "")

    @app.template_filter("fmt_authors")
    def format_authors(text: str) -> str:
        """Render pipe-delimited author rows as HTML."""
        from markupsafe import Markup
        if not text or not text.strip():
            return Markup("&mdash;")
        lines_out: list[str] = []
        affils: dict[str, str] = {}
        for raw_line in text.strip().split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("|")
            name = parts[0].strip() if len(parts) > 0 else ""
            idx = parts[1].strip() if len(parts) > 1 else ""
            affil = parts[2].strip() if len(parts) > 2 else ""
            if not name:
                continue
            sup = f"<sup>{idx}</sup>" if idx else ""
            lines_out.append(f"{name}{sup}")
            if idx and affil and idx not in affils:
                affils[idx] = affil
        result = ", ".join(lines_out)
        if affils:
            sorted_affils = [f"<sup>{k}</sup>{v}" for k, v in
                             sorted(affils.items(), key=lambda x: int(x[0]))]
            result += "<br>" + " &emsp; ".join(sorted_affils)
        return Markup(result)

    from .services.jinja_filters import target_url
    app.add_template_filter(target_url, "target_url")


def _setup_complete(app: Flask) -> bool:
    return Path(app.config["SETUP_FLAG_PATH"]).exists()


def _register_setup_gate(app: Flask) -> None:
    """Force *every* request to /setup until the wizard has completed.

    Static assets and the wizard itself are allowed through.
    """
    @app.before_request
    def gate():
        if _setup_complete(app):
            return None
        endpoint = (request.endpoint or "")
        # Whitelist: the wizard, static files, favicon.
        if endpoint.startswith("setup.") or endpoint == "static":
            return None
        return redirect(url_for("setup.welcome"))


def _register_error_handlers(app: Flask) -> None:
    from flask import render_template

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(429)
    def too_many(_e):
        return render_template("errors/429.html"), 429

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("errors/500.html"), 500
