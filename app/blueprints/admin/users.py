"""Admin → Members: view, change role, soft-delete.

Admin role is fixed-true: it can never be set or unset from the UI.
Promotion to admin happens through the `admin_cli.py` script on the host.
"""
from __future__ import annotations

from datetime import datetime

from flask import flash, redirect, render_template, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import User
from ...models.user import EDITABLE_ROLE_NAMES
from ...security import requires_permission, audit


@admin_bp.route("/users", methods=["GET", "POST"])
@requires_permission("users.view", "users.edit")
def users():
    if request.method == "POST":
        if not _can_edit():
            flash("You don't have permission to edit users.", "error")
            return redirect(url_for("admin.users"))
        action = request.form.get("action")
        if action == "set_role":
            try:
                u = User.query.get(int(request.form.get("user_id", "")))
            except (TypeError, ValueError):
                u = None
            new_role = request.form.get("role", "")
            if not u:
                flash("User not found.", "error")
            elif u.role_name == "admin" or new_role not in EDITABLE_ROLE_NAMES:
                flash("That role change isn't permitted here.", "error")
            else:
                old = u.role_name
                u.role_name = new_role
                db.session.commit()
                audit.record("user.role_changed",
                             target_kind="user", target_id=u.id,
                             summary=f"{u.email}: {old} → {new_role}")
                flash(f"{u.email} → {new_role}.", "success")
        elif action == "delete":
            ids = request.form.getlist("user_ids")
            removed = 0
            for raw in ids:
                try:
                    u = User.query.get(int(raw))
                except (TypeError, ValueError):
                    continue
                if not u or u.role_name == "admin":
                    continue
                u.deleted_at = datetime.utcnow()
                removed += 1
            db.session.commit()
            audit.record("user.deleted",
                         summary=f"Soft-deleted {removed} user(s)")
            flash(f"Soft-deleted {removed} user(s).", "success")
        return redirect(url_for("admin.users",
                                role=request.form.get("filter_role", "all")))

    role_filter = request.args.get("role", "all")
    q = User.query.filter(User.deleted_at.is_(None))
    if role_filter in ("admin",) + EDITABLE_ROLE_NAMES:
        q = q.filter_by(role_name=role_filter)
    items = q.order_by(User.role_name, User.email).all()
    return render_template("admin/users.html",
                           items=items,
                           role_filter=role_filter,
                           editable_roles=EDITABLE_ROLE_NAMES)


def _can_edit() -> bool:
    from flask_login import current_user
    return (current_user.is_admin
            or current_user.has_permission("users.edit"))
