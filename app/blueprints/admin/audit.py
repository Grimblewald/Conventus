"""Admin → Audit log viewer."""
from __future__ import annotations

from flask import render_template, request

from . import admin_bp
from ...models import AuditLog
from ...security import admin_required


@admin_bp.route("/audit")
@admin_required
def audit_log():
    page = max(1, int(request.args.get("page", 1) or 1))
    per_page = 50
    q = (AuditLog.query
         .order_by(AuditLog.created_at.desc()))
    action = (request.args.get("action") or "").strip()
    if action:
        q = q.filter(AuditLog.action.like(f"%{action}%"))
    actor = (request.args.get("actor") or "").strip()
    if actor:
        q = q.filter(AuditLog.actor_email.like(f"%{actor}%"))

    total = q.count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()
    return render_template(
        "admin/audit.html",
        rows=rows, page=page, per_page=per_page, total=total,
        action=action, actor=actor,
    )
