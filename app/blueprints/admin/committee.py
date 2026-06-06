"""Admin → Committee: add/edit/reorder committee profiles."""
from __future__ import annotations

from datetime import datetime

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from . import admin_bp
from ...extensions import db
from ...models import CommitteeMember, User
from ...security import requires_permission, audit
from ...services.uploads import UploadError, remove_upload, save_image


def _can_edit_member(m: CommitteeMember) -> bool:
    """edit_any beats edit_self; edit_self requires the row to be linked
    to the current user."""
    if current_user.is_admin or current_user.has_permission("committee.edit_any"):
        return True
    if current_user.has_permission("committee.edit_self"):
        return m.user_id == current_user.id
    return False


@admin_bp.route("/committee")
@requires_permission("committee.edit_self", "committee.edit_any")
def committee_index():
    items = CommitteeMember.visible_in_order()
    return render_template("admin/committee.html", items=items)


@admin_bp.route("/committee/new", methods=["GET", "POST"])
@requires_permission("committee.edit_any")
def committee_new():
    return _committee_form(None)


@admin_bp.route("/committee/<int:mid>/edit", methods=["GET", "POST"])
@requires_permission("committee.edit_self", "committee.edit_any")
def committee_edit(mid):
    m = CommitteeMember.query.get_or_404(mid)
    if not _can_edit_member(m):
        flash("You can't edit that profile.", "error")
        return redirect(url_for("admin.committee_index"))
    return _committee_form(m)


def _committee_form(m: CommitteeMember | None):
    is_new = m is None
    if request.method == "POST":
        if is_new:
            m = CommitteeMember(full_name="(new)")
            db.session.add(m)
            db.session.flush()  # get id for upload prefix

        m.title = (request.form.get("title") or "").strip()
        m.full_name = (request.form.get("full_name") or "").strip()
        m.role = (request.form.get("role") or "").strip()
        m.affiliation = (request.form.get("affiliation") or "").strip()
        m.position = (request.form.get("position") or "").strip()
        m.interests = (request.form.get("interests") or "").strip()
        m.orcid = (request.form.get("orcid") or "").strip()
        m.scholar_url = (request.form.get("scholar_url") or "").strip()
        m.website_url = (request.form.get("website_url") or "").strip()
        m.portrait_alt_text = (request.form.get("portrait_alt_text") or "").strip()
        try:
            m.display_order = int(request.form.get("display_order") or 100)
        except ValueError:
            m.display_order = 100

        m.is_contactable = bool(request.form.get("is_contactable"))

        # Dynamic roles/affiliations
        roles_json = []
        for i in range(20):
            role = (request.form.get(f"dyn_role_{i}") or "").strip()
            affil = (request.form.get(f"dyn_affil_{i}") or "").strip()
            deleted = request.form.get(f"dyn_delete_{i}")
            if deleted:
                continue
            if role or affil:
                roles_json.append({"role": role, "affiliation": affil})
        new_roles = request.form.getlist("new_dyn_role[]")
        new_affils = request.form.getlist("new_dyn_affil[]")
        for i, role in enumerate(new_roles):
            role = role.strip()
            affil = (new_affils[i] if i < len(new_affils) else "").strip()
            if role or affil:
                roles_json.append({"role": role, "affiliation": affil})
        m.dynamic_roles = roles_json if roles_json else None

        # Optional user linkage
        try:
            uid = int(request.form.get("user_id") or "")
            u = User.query.get(uid) if uid else None
            m.user_id = u.id if u else None
        except (TypeError, ValueError):
            m.user_id = m.user_id

        if not m.full_name:
            flash("Full name is required.", "error")
            return render_template("admin/committee_edit.html",
                                   m=m, users=_picklist_users())

        # Portrait
        f = request.files.get("portrait")
        if f and f.filename:
            try:
                rel = save_image(
                    f,
                    upload_folder=current_app.config["UPLOAD_FOLDER"],
                    subdir="committee",
                    prefix=f"m{m.id}",
                    max_bytes=current_app.config["MAX_HERO_BYTES"],
                    square_crop=True,
                    target_size=600,
                )
            except UploadError as e:
                flash(str(e), "error")
                return render_template("admin/committee_edit.html",
                                       m=m, users=_picklist_users())
            if m.portrait_filename:
                remove_upload(current_app.config["UPLOAD_FOLDER"],
                              f"committee/{m.portrait_filename}")
            m.portrait_filename = rel.split("/", 1)[-1]
        elif request.form.get("remove_portrait"):
            if m.portrait_filename:
                remove_upload(current_app.config["UPLOAD_FOLDER"],
                              f"committee/{m.portrait_filename}")
            m.portrait_filename = None

        db.session.commit()
        audit.record(
            "committee.created" if is_new else "committee.updated",
            target_kind="committee_member", target_id=m.id,
            summary=f"{'Created' if is_new else 'Updated'} {m.full_name}",
        )
        flash(f"{'Created' if is_new else 'Saved'} {m.full_name}.", "success")
        return redirect(url_for("admin.committee_index"))

    return render_template("admin/committee_edit.html",
                           m=m, users=_picklist_users())


def _picklist_users():
    return (User.query
            .filter(User.deleted_at.is_(None))
            .order_by(User.full_name, User.email).all())


@admin_bp.route("/committee/<int:mid>/delete", methods=["POST"])
@requires_permission("committee.edit_any")
def committee_delete(mid):
    m = CommitteeMember.query.get_or_404(mid)
    m.deleted_at = datetime.utcnow()
    db.session.commit()
    audit.record("committee.deleted",
                 target_kind="committee_member", target_id=m.id,
                 summary=f"Soft-deleted {m.full_name}")
    flash(f"Removed {m.full_name}.", "success")
    return redirect(url_for("admin.committee_index"))


@admin_bp.route("/committee/reorder", methods=["POST"])
@requires_permission("committee.edit_any")
def committee_reorder():
    """Accepts repeated `id` fields in display order — simple up/down moves."""
    for idx, raw in enumerate(request.form.getlist("id")):
        try:
            m = CommitteeMember.query.get(int(raw))
        except (TypeError, ValueError):
            continue
        if m:
            m.display_order = (idx + 1) * 10
    db.session.commit()
    audit.record("committee.reordered", summary="Committee reordered")
    return redirect(url_for("admin.committee_index"))
