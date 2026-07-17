"""Admin → Permissions: pick a role and tick which permissions it gets.

The Admin role is fixed-true and not editable here. Every other role
defaults to *no* permissions; an admin must explicitly grant each one.
"""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import Role, RolePermission
from ...models.user import (
    BUILT_IN_PERMISSIONS, EDITABLE_ROLE_NAMES, IMPLICIT_PERMISSIONS,
)
from ...security import admin_required, audit


def _grouped_permissions():
    """Return the permission catalogue grouped by section, for the form."""
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for key, group, label, desc in BUILT_IN_PERMISSIONS:
        groups.setdefault(group, []).append((key, label, desc))
    # Preserve catalogue order
    return groups


@admin_bp.route("/permissions", methods=["GET", "POST"])
@admin_required
def permissions():
    role_name = (request.args.get("role")
                 or request.form.get("role")
                 or "member").strip()
    if role_name not in EDITABLE_ROLE_NAMES:
        role_name = "member"
    role: Role = db.session.get(Role, role_name)
    if not role:
        flash("Unknown role.", "error")
        return redirect(url_for("admin.permissions"))

    if request.method == "POST":
        wanted = set(request.form.getlist("perm"))
        # Granting a key also grants any permissions it implies.
        for key in list(wanted):
            wanted.update(IMPLICIT_PERMISSIONS.get(key, ()))
        existing = role.permission_keys()

        # Grant additions
        for key in wanted - existing:
            db.session.add(RolePermission(role_name=role.name, permission_key=key))
        # Revoke removals
        if existing - wanted:
            (RolePermission.query
             .filter(RolePermission.role_name == role.name,
                     RolePermission.permission_key.in_(existing - wanted))
             .delete(synchronize_session=False))
        db.session.commit()
        audit.record("role.permissions_updated",
                     target_kind="role", target_id=role.name,
                     summary=f"{role.label}: {len(wanted)} permission(s)",
                     metadata={"granted": sorted(wanted)})
        flash(f"Permissions for {role.label} saved.", "success")
        return redirect(url_for("admin.permissions", role=role.name))

    granted = role.permission_keys()
    return render_template(
        "admin/permissions.html",
        role=role,
        role_choices=[(n, db.session.get(Role, n).label)
                      for n in EDITABLE_ROLE_NAMES],
        groups=_grouped_permissions(),
        granted=granted,
    )
