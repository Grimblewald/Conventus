"""Admin → Navigation: CRUD + reorder for top-level nav items."""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import NavItem, Page
from ...security import requires_permission, audit
from ...services.targets import label_choices


@admin_bp.route("/nav", methods=["GET", "POST"])
@requires_permission("nav.edit")
def nav():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            n = NavItem(
                label=(request.form.get("label") or "").strip() or "Item",
                target=(request.form.get("target") or "home").strip(),
                display_order=(NavItem.query.count() + 1) * 10,
                open_in_new_tab=bool(request.form.get("open_in_new_tab")),
            )
            db.session.add(n)
            db.session.commit()
            audit.record("nav.added", target_kind="nav_item", target_id=n.id,
                         summary=f"Added nav item “{n.label}”")
            flash("Nav item added.", "success")
        elif action == "save":
            ids = request.form.getlist("id")
            for raw in ids:
                try:
                    item = NavItem.query.get(int(raw))
                except (TypeError, ValueError):
                    continue
                if not item:
                    continue
                item.label = (request.form.get(f"label_{raw}") or item.label).strip()
                item.target = (request.form.get(f"target_{raw}") or item.target).strip()
                item.visible = bool(request.form.get(f"visible_{raw}"))
                item.open_in_new_tab = bool(request.form.get(f"new_tab_{raw}"))
                try:
                    item.display_order = int(request.form.get(f"order_{raw}") or 0)
                except ValueError:
                    pass
            db.session.commit()
            audit.record("nav.updated", target_kind="nav_item",
                         summary="Saved navigation items")
            flash("Navigation saved.", "success")
        elif action == "delete":
            try:
                n = NavItem.query.get(int(request.form.get("id", "")))
                if n:
                    label = n.label
                    db.session.delete(n)
                    db.session.commit()
                    audit.record("nav.deleted", target_kind="nav_item",
                                 target_id=request.form.get("id"),
                                 summary=f"Deleted “{label}”")
                    flash("Removed.", "success")
            except (TypeError, ValueError):
                pass
        return redirect(url_for("admin.nav"))

    items = (NavItem.query
             .order_by(NavItem.display_order.asc(), NavItem.id.asc())
             .all())
    pages = (Page.query
             .filter(Page.deleted_at.is_(None), Page.published.is_(True))
             .order_by(Page.title).all())
    targets = label_choices(pages)
    return render_template("admin/nav.html", items=items, target_choices=targets)
