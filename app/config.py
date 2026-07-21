"""Runtime configuration.

Settings come from environment variables (12-factor); a single `.env` file
in the project root is loaded automatically at import time by `wsgi.py` so
this module never has to know it exists.

We expose three named configs:

* `BaseConfig` — defaults shared by every environment
* `DevelopmentConfig` — relaxed cookies, debug-friendly defaults
* `ProductionConfig` — strict cookies, refuses to start with the dummy secret

Select with `FLASK_ENV=production` (default) or `FLASK_ENV=development`.
"""
from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTANCE_DIR = PROJECT_ROOT / "instance"
DEFAULT_UPLOAD_DIR = PROJECT_ROOT / "uploads"


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _database_uri() -> str:
    explicit = (os.environ.get("DATABASE_URL") or "").strip()
    if explicit:
        # SQLAlchemy 2.x doesn't accept the bare "postgres://" prefix that
        # some hosting providers still emit — normalise it.
        if explicit.startswith("postgres://"):
            explicit = explicit.replace("postgres://", "postgresql+psycopg://", 1)
        elif explicit.startswith("postgresql://") and "+psycopg" not in explicit:
            explicit = explicit.replace("postgresql://", "postgresql+psycopg://", 1)
        return explicit
    # SQLite default — kept in instance/ so it isn't accidentally committed
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{INSTANCE_DIR / 'app.db'}"


class BaseConfig:
    # --- Flask --------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me")

    # --- Database -----------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Pool sizing matters for Postgres under bursty registration windows.
    # SQLite ignores most of these but the keys are harmless.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": _int("DB_POOL_RECYCLE", 1800),
        "pool_size": _int("DB_POOL_SIZE", 5),
        "max_overflow": _int("DB_MAX_OVERFLOW", 10),
    }

    # --- Uploads ------------------------------------------------------------
    # ALWAYS store an absolute path. Werkzeug's send_from_directory uses
    # realpath() + containment check, which is sensitive to the worker's
    # CWD — a relative UPLOAD_FOLDER in .env (e.g. "./uploads") writes
    # files fine but serves them back as 404 once Werkzeug can't prove the
    # resolved file lives inside the resolved directory.
    UPLOAD_FOLDER = str(
        Path(os.environ.get("UPLOAD_FOLDER") or DEFAULT_UPLOAD_DIR).resolve()
    )
    # App-wide hard cap. Per-route caps (smaller) live in the upload helpers.
    # Backup restores can legitimately exceed the old 16 MB cap, so we set a
    # generous ceiling here — every other upload is gated by save_image/save_pdf.
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB
    MAX_HERO_BYTES = 4 * 1024 * 1024       # 4 MB hero/logo/favicon
    MAX_FIGURE_BYTES = 8 * 1024 * 1024     # 8 MB abstract figure
    MAX_BOOKLET_BYTES = 12 * 1024 * 1024   # 12 MB conference booklet PDF

    # --- Auth / OTP ---------------------------------------------------------
    OTP_TTL_SECONDS = _int("OTP_TTL_SECONDS", 10 * 60)
    OTP_MAX_ATTEMPTS = _int("OTP_MAX_ATTEMPTS", 5)
    OTP_LOCKOUT_SECONDS = _int("OTP_LOCKOUT_SECONDS", 15 * 60)

    # --- Rate limiting ------------------------------------------------------
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    RATELIMIT_DEFAULT = "240 per hour;30 per minute"

    # --- Sessions / cookies -------------------------------------------------
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", default=True)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", default=True)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_DURATION = 60 * 60 * 24 * 30  # 30 days

    # --- Babel / i18n -------------------------------------------------------
    BABEL_DEFAULT_LOCALE = "en"
    BABEL_DEFAULT_TIMEZONE = "UTC"
    BABEL_TRANSLATION_DIRECTORIES = str(PROJECT_ROOT / "app" / "translations")

    # --- Mail (read by services.mail at send time) --------------------------
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "console").lower()
    MAIL_FROM = os.environ.get("MAIL_FROM", "Name Your Society <noreply@your-domain.example.org>")

    # --- Document rendering (tectonic compile queue, plan §6) ---------------
    # Concurrent tectonic processes. Default 1 — the VPS is small and tectonic
    # is CPU/RAM-heavy; bump only on a bigger host. Total wall-clock a request
    # waits for its PDF (queue wait + compile) before giving up with an error.
    DOC_COMPILE_WORKERS = _int("DOC_COMPILE_WORKERS", 1)
    DOC_COMPILE_TIMEOUT = _int("DOC_COMPILE_TIMEOUT", 120)

    # --- Update checker (optional) ------------------------------------------
    UPDATE_REMOTE_URL = (os.environ.get("UPDATE_REMOTE_URL") or "").strip()
    UPDATE_BRANCH = (os.environ.get("UPDATE_BRANCH") or "main").strip()

    # --- Misc ---------------------------------------------------------------
    # NB: build the str-form paths BEFORE stringifying the Paths; otherwise
    # the `INSTANCE_DIR / "..."` operations below explode with
    # `unsupported operand type(s) for /: 'str' and 'str'`.
    SETUP_FLAG_PATH = str(INSTANCE_DIR / ".setup-complete")
    SETUP_PASSWORD_PATH = str(INSTANCE_DIR / "setup-pw")
    PROJECT_ROOT = str(PROJECT_ROOT)
    INSTANCE_DIR = str(INSTANCE_DIR)


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    # Don't require redis in dev
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False

    @classmethod
    def assert_safe(cls) -> None:
        """Refuse to launch in production with developer-default secrets."""
        if cls.SECRET_KEY in (None, "", "dev-change-me",
                              "CHANGE-ME-generate-with-secrets.token_urlsafe-48"):
            raise RuntimeError(
                "SECRET_KEY is unset or still the placeholder. Generate one with "
                "`python -c 'import secrets; print(secrets.token_urlsafe(48))'` "
                "and set it in your .env before launching in production."
            )


def select_config() -> type[BaseConfig]:
    # Default to development so `gunicorn wsgi:app` on a fresh checkout
    # doesn't pick ProductionConfig (which forces HTTPS over a plain-HTTP
    # local server and produces a redirect loop). Production deployments
    # are expected to set FLASK_ENV=production explicitly — which also
    # triggers `assert_safe()` and refuses to launch with the placeholder
    # SECRET_KEY.
    env = (os.environ.get("FLASK_ENV") or "development").strip().lower()
    if env in ("prod", "production"):
        ProductionConfig.assert_safe()
        return ProductionConfig
    return DevelopmentConfig
