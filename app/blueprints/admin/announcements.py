"""Admin → Announcements: CRUD with soft delete."""
from __future__ import annotations

from datetime import datetime

from flask import flash, redirect, render_template, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import Announcement
from ...models.announcement import ANN_KINDS
from ...security import requires_permission, audit


@admin_bp.route("/announcements", methods=["GET", "POST"])
@requires_permission("ann.publish")
def announcements():
    if request.method == "POST":
        a = Announcement(
            title=(request.form.get("title") or "").strip(),
            kind=(request.form.get("kind") or "News").strip(),
            body=(request.form.get("body") or "").strip(),
            pinned=bool(request.form.get("pinned")),
        )
        if a.kind not in ANN_KINDS:
            a.kind = "News"
        if not a.title:
            flash("Title is required.", "error")
        else:
            db.session.add(a)
            db.session.commit()
            audit.record("announcement.created",
                         target_kind="announcement", target_id=a.id,
                         summary=f"+ “{a.title}”")
            flash("Announcement posted.", "success")
        return redirect(url_for("admin.announcements"))
    items = (
        Announcement.query
        .filter(Announcement.deleted_at.is_(None))
        .order_by(Announcement.pinned.desc(), Announcement.published_at.desc())
        .all()
    )
    return render_template("admin/announcements.html", items=items, kinds=ANN_KINDS)


@admin_bp.route("/announcements/<int:aid>/edit", methods=["GET", "POST"])
@requires_permission("ann.publish")
def announcement_edit(aid):
    a = Announcement.query.get_or_404(aid)
    if request.method == "POST":
        a.title = (request.form.get("title") or "").strip()
        a.kind = (request.form.get("kind") or "News").strip()
        if a.kind not in ANN_KINDS:
            a.kind = "News"
        a.body = (request.form.get("body") or "").strip()
        a.pinned = bool(request.form.get("pinned"))
        db.session.commit()
        audit.record("announcement.updated",
                     target_kind="announcement", target_id=a.id,
                     summary=f"Edited “{a.title}”")
        flash("Announcement updated.", "success")
        return redirect(url_for("admin.announcements"))
    return render_template("admin/announcement_edit.html", a=a, kinds=ANN_KINDS)


@admin_bp.route("/announcements/<int:aid>/delete", methods=["POST"])
@requires_permission("ann.delete")
def announcement_delete(aid):
    a = Announcement.query.get_or_404(aid)
    a.deleted_at = datetime.utcnow()
    db.session.commit()
    audit.record("announcement.deleted",
                 target_kind="announcement", target_id=a.id,
                 summary=f"Soft-deleted “{a.title}”")
    flash("Announcement removed.", "success")
    return redirect(url_for("admin.announcements"))
