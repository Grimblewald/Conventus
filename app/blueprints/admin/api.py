"""Admin API endpoints consumed by the admin JS (role change, member search)."""

from __future__ import annotations

from flask import jsonify, request
from sqlalchemy import or_

from . import admin_bp
from ...extensions import db
from ...models import User
from ...models.user import EDITABLE_ROLE_NAMES
from ...security import requires_permission, audit


@admin_bp.route("/api/users/<int:user_id>/role", methods=["POST"])
@requires_permission("users.edit")
def api_set_role(user_id: int):
    u = db.session.get(User, user_id)
    if not u:
        return jsonify({"error": "User not found."}), 404
    if u.role_name == "admin":
        return jsonify({"error": "Cannot change the Administrator role."}), 403

    data = request.get_json(silent=True) or {}
    new_role = (data.get("role") or "").strip()
    if new_role not in EDITABLE_ROLE_NAMES:
        return jsonify({"error": "Invalid role."}), 400

    old = u.role_name
    u.role_name = new_role
    db.session.commit()
    audit.record(
        "user.role_changed",
        target_kind="user", target_id=u.id,
        summary=f"{u.email}: {old} -> {new_role}",
    )
    return jsonify({"success": True, "role": new_role})


@admin_bp.route("/api/users/search")
@requires_permission("users.edit", "users.view")
def api_search_users():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])

    pattern = f"%{q}%"
    results = (
        User.query
        .filter(
            User.deleted_at.is_(None),
            User.role_name != "admin",
            or_(
                User.email.ilike(pattern),
                User.full_name.ilike(pattern),
            ),
        )
        .order_by(User.full_name, User.email)
        .limit(10)
        .all()
    )
    return jsonify([
        {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name or "",
            "role_name": u.role_name,
        }
        for u in results
    ])
