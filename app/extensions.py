"""Shared Flask extension singletons.

Importing extension objects from one place avoids circular-import headaches:
modules call `from app.extensions import db` and never reach into the
factory module.
"""
from __future__ import annotations

from flask_babel import Babel
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_talisman import Talisman
from flask_wtf.csrf import CSRFProtect


db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
talisman = Talisman()
babel = Babel()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["240 per hour", "30 per minute"],
    headers_enabled=True,
)

login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "error"
