"""Admin → Navigation: CRUD + reorder for top-level nav items."""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import NavItem, Page
from ...security import requires_permission, audit
from ...services.targets import build_target, label_choices


@admin_bp.route("/nav", methods=["GET", "POST"])
@requires_permission("nav.edit")
def nav():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            try:
                target = build_target(request.form.get("target"),
                                      request.form.get("target_url"))
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("admin.nav"))
            n = NavItem(
                label=(request.form.get("label") or "").strip() or "Item",
                target=target,
                display_order=(NavItem.query.count() + 1) * 10,
                open_in_new_tab=bool(request.form.get("open_in_new_tab")),
            )
            db.session.add(n)
            db.session.commit()
            audit.record("nav.added", target_kind="nav_item", target_id=n.id,
                         summary=f"Added nav item “{n.label}”")
            flash("Nav item added.", "success")
        elif action == "save":
            errors: list[str] = []
            ids = request.form.getlist("id")
            for raw in ids:
                try:
                    item = NavItem.query.get(int(raw))
                except (TypeError, ValueError):
                    continue
                if not item:
                    continue
                item.label = (request.form.get(f"label_{raw}") or item.label).strip()
                try:
                    item.target = build_target(
                        request.form.get(f"target_{raw}"),
                        request.form.get(f"target_url_{raw}"),
                        fallback=item.target)
                except ValueError as e:
                    # One bad URL must not silently drop the other edits, nor
                    # be written as a broken link. Keep the old target, say so.
                    errors.append(f"{item.label}: {e}")
                item.visible = bool(request.form.get(f"visible_{raw}"))
                item.open_in_new_tab = bool(request.form.get(f"new_tab_{raw}"))
                try:
                    item.display_order = int(request.form.get(f"order_{raw}") or 0)
                except ValueError:
                    pass
            db.session.commit()
            audit.record("nav.updated", target_kind="nav_item",
                         summary="Saved navigation items")
            for err in errors:
                flash(err, "error")
            flash("Navigation saved." if not errors
                  else "Navigation saved, except the links noted above.",
                  "success" if not errors else "warning")
        return redirect(url_for("admin.nav"))

    items = (NavItem.query
             .order_by(NavItem.display_order.asc(), NavItem.id.asc())
             .all())
    pages = (Page.query
             .filter(Page.deleted_at.is_(None), Page.published.is_(True))
             .order_by(Page.title).all())
    targets = label_choices(pages)
    return render_template("admin/nav.html", items=items, target_choices=targets)


@admin_bp.route("/nav/<int:item_id>/delete", methods=["POST"])
@requires_permission("nav.edit")
def nav_delete(item_id: int):
    item = NavItem.query.get_or_404(item_id)
    label = item.label
    db.session.delete(item)
    db.session.commit()
    audit.record("nav.deleted", target_kind="nav_item", target_id=item_id,
                 summary=f"Deleted “{label}”")
    flash(f"Removed “{label}”.", "success")
    return redirect(url_for("admin.nav"))
