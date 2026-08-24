"""Shared pytest fixtures for the Society Site test suite."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-key-do-not-use-in-production")
os.environ.setdefault("MAIL_BACKEND", "console")
os.environ.setdefault("RATELIMIT_STORAGE_URI", "memory://")
os.environ.setdefault("RATELIMIT_ENABLED", "false")

from app.config import BaseConfig, DevelopmentConfig
BaseConfig.RATELIMIT_ENABLED = False
DevelopmentConfig.RATELIMIT_ENABLED = False

from app.extensions import db as _db


@pytest.fixture(autouse=True)
def stub_latex_compile(request, monkeypatch):
    """Replace the tectonic run with a fake, unless the test asks for the real
    one with the `real_latex` marker.

    Sending an abstract or settling a payment attaches a rendered PDF, so most
    of the suite compiled LaTeX as a side effect of exercising something else —
    a few seconds each, for bytes nothing then looked at.

    The fake is content-sensitive: the same .tex always yields the same bytes
    and different .tex different bytes, so tests that rely on a document being
    rebuilt identically still mean something.
    """
    if "real_latex" in request.keywords:
        return

    import hashlib

    from app.services import documents as docs

    def _fake_compile(tectonic, job_dir, tex_path, epoch, memory_mb=0,
                      should_abort=None, timeout=0):
        digest = hashlib.sha256(Path(tex_path).read_bytes()).hexdigest()
        return b"%PDF-" + digest.encode()

    monkeypatch.setattr(docs, "_compile", _fake_compile)


@pytest.fixture(scope="session")
def app():
    """Session-scoped Flask app with a temp instance and DB.

    Overrides the DB URI at the config-class level *before* create_app()
    so that _init_extensions → db.create_all() targets the temp file
    rather than the live instance/app.db.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_uri = f"sqlite:///{tmp}/app.db"
        from app.config import BaseConfig, DevelopmentConfig
        # Override class-level defaults so create_app() picks up the temp path.
        BaseConfig.SQLALCHEMY_DATABASE_URI = db_uri
        DevelopmentConfig.SQLALCHEMY_DATABASE_URI = db_uri

        from app import create_app
        app = create_app()

        app.instance_path = tmp
        app.config.update({
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SERVER_NAME": "localhost",
            "PREFERRED_URL_SCHEME": "http",
            "RATELIMIT_ENABLED": False,
        })
        app.config["UPLOAD_FOLDER"] = str(Path(tmp) / "uploads")
        # Keep rendered-document caches and job dirs out of the project tree,
        # so a run cannot be served results cached by an earlier one.
        app.config["DOC_RENDER_ROOT"] = str(Path(tmp) / "doc-render")
        app.config["SETUP_FLAG_PATH"] = str(Path(tmp) / ".setup-complete")
        app.config["SETUP_PASSWORD_PATH"] = str(Path(tmp) / "setup-pw")
        Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
        Path(tmp).mkdir(parents=True, exist_ok=True)

        with app.app_context():
            _db.create_all()
            from app.models.user import ensure_roles_exist
            ensure_roles_exist()
            # Minimal site settings so get_site_settings() works everywhere.
            from app.models.content import SiteSettings
            if not SiteSettings.query.first():
                s = SiteSettings(id=1, site_name="Test Society",
                                 tagline="Test", short_name="Test",
                                 browser_tab_title="Test Society")
                _db.session.add(s)
                _db.session.commit()
            # Touch the setup-complete flag so the before_request gate passes.
            Path(app.config["SETUP_FLAG_PATH"]).touch()
            # Create a default admin so contact forms and admin tests have a recipient.
            from app.models import User
            if not User.query.filter_by(role_name="admin").first():
                u = User(email="admin@test.example.org", full_name="Test Admin",
                         role_name="admin")
                _db.session.add(u)
                _db.session.commit()

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


@pytest.fixture
def login_user_session(client, app):
    """Log in a user by directly setting the Flask-Login session.

    Returns the User instance so tests can assert on user properties.
    To use, pass the desired role_name kwarg via indirect parametrization
    or call _make_and_login() helper within your test.
    """
    from app.models import User

    created: list[User] = []

    def _login(email="test@example.org", full_name="Test User",
               role_name="member", affiliation="Test Uni"):
        with app.app_context():
            u = User.query.filter_by(email=email).first()
            if u is None:
                u = User(email=email, full_name=full_name,
                         role_name=role_name, affiliation=affiliation)
                _db.session.add(u)
                _db.session.commit()
            user_id = u.id

        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        return user_id

    yield _login


@pytest.fixture
def member_client(client, login_user_session):
    """A test client logged in as a member."""
    login_user_session(email="member@test.example.org",
                       full_name="Member User", role_name="member")
    return client


@pytest.fixture
def admin_client(client, login_user_session):
    """A test client logged in as an admin."""
    login_user_session(email="admin@test.example.org",
                       full_name="Admin User", role_name="admin")
    return client


@pytest.fixture
def committee_client(client, login_user_session, app):
    """A test client logged in as a committee member, with committee.edit_self."""
    from app.models import Role, RolePermission

    user_id = login_user_session(email="committee@test.example.org",
                                 full_name="Committee User", role_name="committee")

    with app.app_context():
        r = _db.session.get(Role, "committee")
        if r and not any(p.permission_key == "committee.edit_self" for p in r.permissions):
            _db.session.add(RolePermission(role_name="committee",
                                           permission_key="committee.edit_self"))
            _db.session.commit()

    return client, user_id
