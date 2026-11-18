"""Admin → Conference → Registration report, on screen and as a PDF.

Counts only: see app/services/registration_report.py. Both views read the
same report, so the PDF is exactly what the page shows.
"""
from __future__ import annotations

from io import BytesIO

from flask import flash, redirect, render_template, request, send_file, url_for
from markupsafe import Markup

from . import admin_bp
from ...models import Conference
from ...models.content import get_site_settings
from ...security import requires_permission
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
    return render_template(
        "admin/conference_report.html", c=c, report=report, sankey=sankey,
        sankey_svg=Markup(rr.svg(sankey)), stages=stages,
        stage_options=rr.stage_options(c), percent=rr.percent,
        site=get_site_settings())


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
