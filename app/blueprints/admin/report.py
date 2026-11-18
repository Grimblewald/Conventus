"""Admin → Conference → Registration report, on screen and as a PDF.

Counts only: see app/services/registration_report.py. Both views read the
same report, so the PDF is exactly what the page shows.

The page also offers the attendee list, which is not counts: a CSV with
names, columns chosen by the admin (app/services/attendee_export.py).
"""
from __future__ import annotations

from io import BytesIO

from flask import flash, redirect, render_template, request, send_file, url_for
from markupsafe import Markup

from . import admin_bp
from ...models import Conference
from ...models.content import get_site_settings
from ...security import requires_permission
from ...services import attendee_export
from ...services import registration_report as rr


def _conference(cid: int) -> Conference:
    c = Conference.query.get_or_404(cid)
    if c.deleted_at is not None:
        from flask import abort
        abort(404)
    return c


@admin_bp.route("/conferences/<int:cid>/report")
@requires_permission("registrations.view")
def conference_report(cid):
    c = _conference(cid)
    stages = rr.parse_stages(c, request.args.getlist("stage"))
    report = rr.build(c)
    sankey = rr.build_sankey(c, stages)
    chosen, include_inactive = attendee_export.saved(c)
    return render_template(
        "admin/conference_report.html", c=c, report=report, sankey=sankey,
        sankey_svg=Markup(rr.svg(sankey)), stages=stages,
        stage_options=rr.stage_options(c), percent=rr.percent,
        site=get_site_settings(),
        export_columns=attendee_export.columns(c), export_chosen=chosen,
        export_include_inactive=include_inactive)


@admin_bp.route("/conferences/<int:cid>/attendees.csv", methods=["POST"])
@requires_permission("registrations.view")
def conference_attendees_csv(cid):
    """Save the ticked columns on the conference, then download the list.

    "Reset" puts the suggested columns back without downloading anything.
    """
    from ...security import audit

    c = _conference(cid)
    if request.form.get("reset"):
        c.attendee_export = None
        from ...extensions import db
        db.session.commit()
        flash("The attendee list's columns are back to the suggested ones.", "success")
        return redirect(url_for("admin.conference_report", cid=c.id) + "#attendee-list")

    offered = [col.key for col in attendee_export.columns(c)]
    picked = set(request.form.getlist("col"))
    keys = [k for k in offered if k in picked]
    include_inactive = bool(request.form.get("include_inactive"))
    if not keys:
        flash("Tick at least one column for the attendee list.", "error")
        return redirect(url_for("admin.conference_report", cid=c.id) + "#attendee-list")

    choice = {"columns": keys, "include_inactive": include_inactive}
    if c.attendee_export != choice:
        from ...extensions import db
        c.attendee_export = choice
        db.session.commit()

    data, n = attendee_export.build_csv(
        c, keys, include_inactive=include_inactive,
        currency_symbol=get_site_settings().currency_symbol or "$")
    audit.record("registrations.exported", target_kind="conference", target_id=c.id,
                 summary=f"Attendee list CSV: {n} registrations, {len(keys)} columns",
                 metadata={"columns": keys, "include_inactive": include_inactive})
    resp = send_file(BytesIO(data), mimetype="text/csv", as_attachment=True,
                     download_name=attendee_export.filename(c))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@admin_bp.route("/conferences/<int:cid>/report.pdf")
@requires_permission("registrations.view")
def conference_report_pdf(cid):
    from ...services.documents import RenderError

    c = _conference(cid)
    stages = rr.parse_stages(c, request.args.getlist("stage"))
    report = rr.build(c)
    # Narrower than the page's: it is drawn at the PDF's text width.
    sankey = rr.build_sankey(c, stages, width=480, height=330)
    try:
        pdf = rr.render_pdf(report, sankey,
                            currency_symbol=get_site_settings().currency_symbol or "$")
    except RenderError as e:
        flash(f"The PDF could not be generated: {e}", "error")
        return redirect(url_for("admin.conference_report", cid=c.id,
                                stage=stages))
    return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=rr.pdf_filename(c, report))


# ---------------------------------------------------------------------------
# The same PDF as a background job, so the page can show progress. The plain
# report.pdf route above stays as the fallback when script is off.
# ---------------------------------------------------------------------------

def _job_for(c: Conference, job_id: str) -> dict:
    """The job, if it is this person's and this conference's; else 404, so
    a job id seen in someone else's browser opens nothing."""
    from flask import abort
    from flask_login import current_user

    from ...services import report_jobs
    status = report_jobs.read(job_id)
    if (status is None or status.get("conference_id") != c.id
            or status.get("user_id") != current_user.id):
        abort(404)
    return status


@admin_bp.route("/conferences/<int:cid>/report/jobs", methods=["POST"])
@requires_permission("registrations.view")
def conference_report_job_start(cid):
    from flask import jsonify
    from flask_login import current_user

    from ...services import report_jobs
    c = _conference(cid)
    stages = rr.parse_stages(c, request.form.getlist("stage"))
    job_id, started = report_jobs.start(c, stages, current_user.id)
    return jsonify(
        job=job_id, started=started,
        status_url=url_for("admin.conference_report_job", cid=c.id, job_id=job_id),
        download_url=url_for("admin.conference_report_job_download",
                             cid=c.id, job_id=job_id)), 202


@admin_bp.route("/conferences/<int:cid>/report/jobs/<job_id>")
@requires_permission("registrations.view")
def conference_report_job(cid, job_id):
    from flask import jsonify

    from ...services import report_jobs
    c = _conference(cid)
    resp = jsonify(report_jobs.public(_job_for(c, job_id)))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@admin_bp.route("/conferences/<int:cid>/report/jobs/<job_id>/download")
@requires_permission("registrations.view")
def conference_report_job_download(cid, job_id):
    from flask import abort

    from ...services import report_jobs
    c = _conference(cid)
    status = _job_for(c, job_id)
    path = report_jobs.pdf_path(job_id)
    if status.get("state") != "done" or path is None or not path.exists():
        abort(404)
    return send_file(path, mimetype="application/pdf", as_attachment=True,
                     download_name=status.get("filename") or "registration-report.pdf")
