"""Admin → Footer: edit columns + their links."""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import FooterColumn, FooterLink, Page
from ...security import requires_permission, audit
from ...services.targets import label_choices


@admin_bp.route("/footer", methods=["GET", "POST"])
@requires_permission("footer.edit")
def footer():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_column":
            c = FooterColumn(
                title=(request.form.get("title") or "Column").strip(),
                display_order=(FooterColumn.query.count() + 1) * 10,
            )
            db.session.add(c)
            db.session.commit()
            audit.record("footer.column_added",
                         target_kind="footer_column", target_id=c.id,
                         summary=f"Added column “{c.title}”")
            flash("Column added.", "success")

        elif action == "delete_column":
            try:
                c = FooterColumn.query.get(int(request.form.get("column_id", "")))
                if c:
                    title = c.title
                    db.session.delete(c)
                    db.session.commit()
                    audit.record("footer.column_deleted", summary=f"Deleted “{title}”")
                    flash("Column removed.", "success")
            except (TypeError, ValueError):
                pass

        elif action == "add_link":
            try:
                col_id = int(request.form.get("column_id", ""))
                col = FooterColumn.query.get(col_id)
            except (TypeError, ValueError):
                col = None
            if col:
                ln = FooterLink(
                    column_id=col.id,
                    label=(request.form.get("label") or "Link").strip(),
                    target=(request.form.get("target") or "home").strip(),
                    display_order=(len(col.links) + 1) * 10,
                    open_in_new_tab=bool(request.form.get("open_in_new_tab")),
                )
                db.session.add(ln)
                db.session.commit()
                audit.record("footer.link_added", target_kind="footer_link",
                             target_id=ln.id, summary=f"+ “{ln.label}”")
                flash("Link added.", "success")

        elif action == "save":
            # Bulk save column titles, orders, and per-link fields.
            for col in FooterColumn.query.all():
                col.title = (request.form.get(f"col_title_{col.id}") or col.title).strip()
                try:
                    col.display_order = int(request.form.get(f"col_order_{col.id}") or 0)
                except ValueError:
                    pass
                for ln in col.links:
                    ln.label = (request.form.get(f"link_label_{ln.id}") or ln.label).strip()
                    ln.target = (request.form.get(f"link_target_{ln.id}") or ln.target).strip()
                    ln.open_in_new_tab = bool(request.form.get(f"link_new_tab_{ln.id}"))
                    try:
                        ln.display_order = int(request.form.get(f"link_order_{ln.id}") or 0)
                    except ValueError:
                        pass
            db.session.commit()
            audit.record("footer.updated", summary="Footer saved")
            flash("Footer saved.", "success")

        elif action == "delete_link":
            try:
                ln = FooterLink.query.get(int(request.form.get("link_id", "")))
                if ln:
                    label = ln.label
                    db.session.delete(ln)
                    db.session.commit()
                    audit.record("footer.link_deleted", summary=f"Deleted “{label}”")
                    flash("Link removed.", "success")
            except (TypeError, ValueError):
                pass

        return redirect(url_for("admin.footer"))

    columns = FooterColumn.visible_in_order()
    pages = (Page.query
             .filter(Page.deleted_at.is_(None), Page.published.is_(True))
             .order_by(Page.title).all())
    targets = label_choices(pages)
    return render_template("admin/footer.html",
                           columns=columns, target_choices=targets)
