"""Admin → Pages: CRUD for Markdown-bodied static pages."""
from __future__ import annotations

from datetime import datetime

from flask import flash, redirect, render_template, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import Page
from ...security import requires_permission, audit
from ...services.slugs import slugify


@admin_bp.route("/pages")
@requires_permission("pages.edit")
def pages():
    items = (
        Page.query
        .filter(Page.deleted_at.is_(None))
        .order_by(Page.title)
        .all()
    )
    return render_template("admin/pages.html", items=items)


@admin_bp.route("/pages/new", methods=["GET", "POST"])
@requires_permission("pages.edit")
def page_new():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        slug = slugify(request.form.get("slug") or title)
        body = (request.form.get("body") or "").strip()
        published = bool(request.form.get("published"))
        if not (title and slug):
            flash("Title and slug are required.", "error")
            return render_template("admin/page_edit.html", p=None, form=request.form)
        if Page.query.filter_by(slug=slug).first():
            flash(f"Slug “{slug}” is already used.", "error")
            return render_template("admin/page_edit.html", p=None, form=request.form)
        p = Page(slug=slug, title=title, body=body, published=published)
        db.session.add(p)
        db.session.commit()
        audit.record("page.created",
                     target_kind="page", target_id=p.id,
                     summary=f"Created page “{title}”")
        flash("Page created.", "success")
        return redirect(url_for("admin.pages"))
    return render_template("admin/page_edit.html", p=None, form={})


@admin_bp.route("/pages/<int:pid>/edit", methods=["GET", "POST"])
@requires_permission("pages.edit")
def page_edit(pid):
    p = Page.query.get_or_404(pid)
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        slug = slugify(request.form.get("slug") or title)
        if not (title and slug):
            flash("Title and slug are required.", "error")
            return render_template("admin/page_edit.html", p=p, form=request.form)
        clash = Page.query.filter(Page.slug == slug, Page.id != p.id).first()
        if clash:
            flash(f"Slug “{slug}” is already used.", "error")
            return render_template("admin/page_edit.html", p=p, form=request.form)
        p.title = title
        p.slug = slug
        p.body = (request.form.get("body") or "").strip()
        p.published = bool(request.form.get("published"))
        db.session.commit()
        audit.record("page.updated",
                     target_kind="page", target_id=p.id,
                     summary=f"Edited page “{title}”")
        flash("Page saved.", "success")
        return redirect(url_for("admin.pages"))
    return render_template("admin/page_edit.html", p=p, form={})


@admin_bp.route("/pages/<int:pid>/delete", methods=["POST"])
@requires_permission("pages.edit", "pages.delete")
def page_delete(pid):
    p = Page.query.get_or_404(pid)
    p.deleted_at = datetime.utcnow()
    db.session.commit()
    audit.record("page.deleted",
                 target_kind="page", target_id=p.id,
                 summary=f"Soft-deleted “{p.title}”")
    flash("Page removed.", "success")
    return redirect(url_for("admin.pages"))
