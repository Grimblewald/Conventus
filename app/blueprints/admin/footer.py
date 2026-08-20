"""Admin → Footer: edit columns + their links."""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import FooterColumn, FooterLink, Page
from ...security import requires_permission, audit
from ...services.targets import build_target, label_choices


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

        elif action == "add_link":
            try:
                col_id = int(request.form.get("column_id", ""))
                col = FooterColumn.query.get(col_id)
            except (TypeError, ValueError):
                col = None
            if col:
                try:
                    target = build_target(request.form.get("target"),
                                          request.form.get("target_url"))
                except ValueError as e:
                    flash(str(e), "error")
                    return redirect(url_for("admin.footer"))
                ln = FooterLink(
                    column_id=col.id,
                    label=(request.form.get("label") or "Link").strip(),
                    target=target,
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
            errors: list[str] = []
            for col in FooterColumn.query.all():
                col.title = (request.form.get(f"col_title_{col.id}") or col.title).strip()
                try:
                    col.display_order = int(request.form.get(f"col_order_{col.id}") or 0)
                except ValueError:
                    pass
                for ln in col.links:
                    ln.label = (request.form.get(f"link_label_{ln.id}") or ln.label).strip()
                    try:
                        ln.target = build_target(
                            request.form.get(f"link_target_{ln.id}"),
                            request.form.get(f"link_target_url_{ln.id}"),
                            fallback=ln.target)
                    except ValueError as e:
                        # Keep the old target and report it; one bad URL must
                        # not discard the rest of the save.
                        errors.append(f"{ln.label}: {e}")
                    ln.open_in_new_tab = bool(request.form.get(f"link_new_tab_{ln.id}"))
                    try:
                        ln.display_order = int(request.form.get(f"link_order_{ln.id}") or 0)
                    except ValueError:
                        pass
            db.session.commit()
            audit.record("footer.updated", summary="Footer saved")
            for err in errors:
                flash(err, "error")
            flash("Footer saved." if not errors
                  else "Footer saved, except the links noted above.",
                  "success" if not errors else "warning")

        return redirect(url_for("admin.footer"))

    columns = FooterColumn.visible_in_order()
    pages = (Page.query
             .filter(Page.deleted_at.is_(None), Page.published.is_(True))
             .order_by(Page.title).all())
    targets = label_choices(pages)
    return render_template("admin/footer.html",
                           columns=columns, target_choices=targets)


@admin_bp.route("/footer/column/<int:column_id>/delete", methods=["POST"])
@requires_permission("footer.edit")
def footer_column_delete(column_id: int):
    """Remove a column and, by cascade, the links inside it."""
    col = FooterColumn.query.get_or_404(column_id)
    title, n = col.title, len(col.links)
    db.session.delete(col)
    db.session.commit()
    audit.record("footer.column_deleted", target_kind="footer_column",
                 target_id=column_id,
                 summary=f"Deleted “{title}” and {n} link(s)")
    flash(f"Removed “{title}”" + (f" and its {n} link(s)." if n else "."),
          "success")
    return redirect(url_for("admin.footer"))


@admin_bp.route("/footer/link/<int:link_id>/delete", methods=["POST"])
@requires_permission("footer.edit")
def footer_link_delete(link_id: int):
    link = FooterLink.query.get_or_404(link_id)
    label = link.label
    db.session.delete(link)
    db.session.commit()
    audit.record("footer.link_deleted", target_kind="footer_link",
                 target_id=link_id, summary=f"Deleted “{label}”")
    flash(f"Removed “{label}”.", "success")
    return redirect(url_for("admin.footer"))
