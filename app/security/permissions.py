"""Permission decorator + helpers.

Every gated route uses `@requires_permission("verb.noun")`. Admins always
pass. The catalogue of permission keys lives in `app.models.user`.
"""
from __future__ import annotations

from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def requires_permission(*keys: str):
    """Require *any* of the listed permission keys. Admin always passes."""
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if getattr(current_user, "is_admin", False):
                return fn(*args, **kwargs)
            if any(current_user.has_permission(k) for k in keys):
                return fn(*args, **kwargs)
            abort(403)
        return wrapper
    return deco


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def staff_required(fn):
    """Admin OR committee. Used for routes that should appear in the admin
    chrome regardless of which permissions each role holds — finer-grained
    checks then happen inside the view.
    """
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not (getattr(current_user, "is_admin", False)
                or getattr(current_user, "is_committee", False)):
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def can(key: str) -> bool:
    """Template helper — `{% if can('users.edit') %}…{% endif %}`."""
    if not current_user.is_authenticated:
        return False
    return current_user.has_permission(key)
