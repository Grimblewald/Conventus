"""Admin → Form Builder for registration and abstract form schemas."""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from . import admin_bp
from ...extensions import db
from ...models import Conference, FormTemplate
from ...security import requires_permission, audit


@admin_bp.route("/conferences/<int:cid>/form-builder/<form_type>",
               methods=["GET", "POST"])
@requires_permission("conf.edit")
def form_builder(cid, form_type):
    """Edit a conference's registration or abstract form schema."""
    if form_type not in ("registration", "abstract"):
        flash("Invalid form type.", "error")
        return redirect(url_for("admin.conferences"))

    c = Conference.query.get_or_404(cid)
    schema = (c.registration_form_schema if form_type == "registration"
              else c.abstract_form_schema) or {"sections": []}

    templates = FormTemplate.query.filter_by(form_type=form_type).order_by(
        FormTemplate.name).all()

    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action == "save_schema":
                _save_schema_from_form(c, form_type)
                flash("Form schema saved.", "success")
                return redirect(url_for("admin.form_builder",
                                        cid=c.id, form_type=form_type))

            elif action == "load_template":
                tid = int(request.form.get("template_id", 0))
                tmpl = FormTemplate.query.get(tid)
                if tmpl and tmpl.form_type == form_type:
                    if form_type == "registration":
                        c.registration_form_schema = tmpl.schema
                    else:
                        c.abstract_form_schema = tmpl.schema
                    db.session.commit()
                    audit.record("conference.form_loaded",
                                 target_kind="conference", target_id=c.id,
                                 summary=f"Loaded {form_type} template “{tmpl.name}” for {c.slug}")
                    flash(f"Loaded template “{tmpl.name}”.", "success")
                else:
                    flash("Template not found.", "error")
                return redirect(url_for("admin.form_builder",
                                        cid=c.id, form_type=form_type))

            elif action == "save_as_template":
                name = (request.form.get("template_name") or "").strip()
                if not name:
                    flash("Template name is required.", "error")
                else:
                    _save_schema_from_form(c, form_type)
                    current_schema = (
                        c.registration_form_schema if form_type == "registration"
                        else c.abstract_form_schema
                    ) or {"sections": []}
                    tmpl = FormTemplate(
                        name=name,
                        form_type=form_type,
                        schema=current_schema,
                        created_by_id=current_user.id,
                    )
                    db.session.add(tmpl)
                    db.session.commit()
                    audit.record("form_template.created",
                                 target_kind="form_template", target_id=tmpl.id,
                                 summary=f"Saved {form_type} template “{name}”")
                    flash(f"Saved as template “{name}”.", "success")
                return redirect(url_for("admin.form_builder",
                                        cid=c.id, form_type=form_type))

            elif action == "delete_template":
                tid = int(request.form.get("template_id", 0))
                tmpl = FormTemplate.query.get(tid)
                if tmpl:
                    name = tmpl.name
                    db.session.delete(tmpl)
                    db.session.commit()
                    flash(f"Deleted template “{name}”.", "success")
                return redirect(url_for("admin.form_builder",
                                        cid=c.id, form_type=form_type))

        except Exception as exc:
            db.session.rollback()
            flash(f"Error: {exc}", "error")
            return redirect(url_for("admin.form_builder",
                                    cid=c.id, form_type=form_type))

    return render_template("admin/form_builder.html",
                           c=c, form_type=form_type, schema=schema,
                           templates=templates)


def _save_schema_from_form(conference, form_type):
    """Parse the form-builder POST data into the conference schema JSON."""
    sections: list[dict] = []

    section_keys = request.form.getlist("section_key[]")
    section_labels = request.form.getlist("section_label[]")
    section_collapsible = request.form.getlist("section_collapsible[]")

    for i, sk in enumerate(section_keys):
        sk = sk.strip()
        if not sk:
            continue
        sec = {
            "key": sk,
            "label": section_labels[i].strip() if i < len(section_labels) else sk,
            "collapsible": str(i) in section_collapsible,
            "toggleable": str(i) in request.form.getlist("section_toggleable[]"),
            "fields": [],
        }

        field_keys = request.form.getlist(f"field_key_{i}[]")
        field_labels = request.form.getlist(f"field_label_{i}[]")
        field_types = request.form.getlist(f"field_type_{i}[]")
        field_required = request.form.getlist(f"field_required_{i}[]")
        field_options = request.form.getlist(f"field_options_{i}[]")
        field_cond_field = request.form.getlist(f"field_cond_field_{i}[]")
        field_cond_value = request.form.getlist(f"field_cond_value_{i}[]")

        for fj, fk in enumerate(field_keys):
            fk = fk.strip()
            if not fk:
                continue
            fdef: dict = {
                "key": fk,
                "label": field_labels[fj].strip() if fj < len(field_labels) else fk,
                "type": field_types[fj].strip() if fj < len(field_types) else "text",
                "required": str(fj) in field_required,
            }
            opts = field_options[fj].strip() if fj < len(field_options) else ""
            if opts:
                fdef["options"] = [o.strip() for o in opts.split("\n") if o.strip()]
            cond_field = field_cond_field[fj].strip() if fj < len(field_cond_field) else ""
            if cond_field:
                cond: dict = {"field": cond_field}
                cv = field_cond_value[fj].strip() if fj < len(field_cond_value) else ""
                if cv:
                    if cv.lower() == "true":
                        cond["value"] = True
                    elif cv.lower() == "false":
                        cond["value"] = False
                    else:
                        cond["contains"] = cv
                fdef["condition"] = cond
            sec["fields"].append(fdef)

        sections.append(sec)

    schema = {"sections": sections}
    if form_type == "registration":
        conference.registration_form_schema = schema
    else:
        conference.abstract_form_schema = schema
    db.session.commit()
