"""Shared pytest fixtures for the Society Site test suite."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-key-do-not-use-in-production")
os.environ.setdefault("MAIL_BACKEND", "console")
os.environ.setdefault("RATELIMIT_STORAGE_URI", "memory://")

from app import create_app
from app.extensions import db as _db


@pytest.fixture(scope="session")
def app():
    """Session-scoped Flask app with a temp instance and DB."""
    app = create_app()
    app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SERVER_NAME": "localhost",
        "PREFERRED_URL_SCHEME": "http",
    })

    with tempfile.TemporaryDirectory() as tmp:
        app.instance_path = tmp
        app.config["UPLOAD_FOLDER"] = str(Path(tmp) / "uploads")
        app.config["SETUP_FLAG_PATH"] = str(Path(tmp) / ".setup-complete")
        app.config["SETUP_PASSWORD_PATH"] = str(Path(tmp) / "setup-pw")
        Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
        Path(tmp).mkdir(parents=True, exist_ok=True)

        with app.app_context():
            _db.create_all()

        yield app

        with app.app_context():
            _db.drop_all()


@pytest.fixture
def client(app):
    """Per-test Flask test client."""
    return app.test_client()


@pytest.fixture
def db(app):
    """Access the database within an app context."""
    with app.app_context():
        yield _db


@pytest.fixture
def seeded(app):
    """Seed the DB with setup data so public routes render."""
    from app.models.content import SiteSettings, NavItem, FooterColumn, FooterLink  # noqa: E402

    with app.app_context():
        s = SiteSettings.query.first() if SiteSettings.query.first() else SiteSettings(id=1)
        if not SiteSettings.query.first():
            _db.session.add(s)
        s.site_name = "Test Society"
        s.tagline = "Test tagline"
        s.short_name = "TestSoc"
        s.browser_tab_title = "Test Society"
        s.copyright_line = "(c) 2026 Test Society"
        s.contact_email = "test@test.example.org"

        if not NavItem.query.filter_by(label="Home").first():
            nav = NavItem(label="Home", target="home", display_order=0)
            _db.session.add(nav)

        if not FooterColumn.query.filter_by(title="Links").first():
            col = FooterColumn(title="Links", display_order=0)
            _db.session.add(col)
            _db.session.flush()
            lnk = FooterLink(column_id=col.id, label="About", target="page:about", display_order=0)
            _db.session.add(lnk)

        _db.session.commit()

        Path(app.config["SETUP_FLAG_PATH"]).touch()

    yield
