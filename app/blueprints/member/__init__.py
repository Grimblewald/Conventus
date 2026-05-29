"""Member-area routes: dashboard, profile, conference registration, abstract
submission. All require login.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template,
    request, send_from_directory, url_for,
)
from flask_login import current_user, login_required

from ...extensions import db
from ...models import Abstract, Conference, Registration
from ...models.conference import PriceTier
from ...security import audit
from ...services.uploads import UploadError, save_figure


member_bp = Blueprint("member", __name__)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@member_bp.route("/dashboard")
@login_required
def dashboard():
    regs = (
        Registration.query
        .filter_by(user_id=current_user.id)
        .filter(Registration.deleted_at.is_(None))
        .order_by(Registration.created_at.desc())
        .all()
    )
    abs_ = (
        Abstract.query
        .filter_by(user_id=current_user.id)
        .filter(Abstract.deleted_at.is_(None))
        .order_by(Abstract.created_at.desc())
        .all()
    )
    return render_template("member/dashboard.html", regs=regs, abstracts=abs_)


@member_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.full_name = (request.form.get("full_name") or "").strip()
        current_user.affiliation = (request.form.get("affiliation") or "").strip()

        # First-time profile completion graduates `unregistered` → `member`.
        if current_user.role_name == "unregistered" and current_user.full_name:
            current_user.role_name = "member"
            audit.record("user.role_changed",
                         target_kind="user", target_id=current_user.id,
                         summary=f"{current_user.email}: unregistered → member")

        db.session.commit()
        flash("Profile saved.", "success")
        return redirect(url_for("member.dashboard"))

    return render_template("member/profile.html")


# ---------------------------------------------------------------------------
# Conference registration
# ---------------------------------------------------------------------------

@member_bp.route("/conferences/<slug>/register", methods=["GET", "POST"])
@login_required
def register_conf(slug):
    c = (Conference.query
         .filter_by(slug=slug)
         .filter(Conference.deleted_at.is_(None))
         .first_or_404())
    if c.is_draft:
        abort(404)
    if not c.accepts_registrations and not c.external_registration_url:
        flash("Registration is not open for this conference.", "error")
        return redirect(url_for("public.conference_detail", slug=c.slug))
    existing = (
        Registration.query
        .filter_by(user_id=current_user.id, conference_id=c.id)
        .filter(Registration.deleted_at.is_(None))
        .first()
    )

    tiers = list(c.price_tiers)

    if request.method == "POST":
        tier_name = (request.form.get("tier") or "").strip()
        tier = next((t for t in tiers if t.name == tier_name), None)
        if not tier:
            flash("Please choose a registration tier.", "error")
            return render_template("member/register_conference.html",
                                   c=c, tiers=tiers, existing=existing)

        reg = existing or Registration(user_id=current_user.id, conference_id=c.id)
        reg.tier_name = tier.name
        reg.amount = tier.amount
        reg.dietary = (request.form.get("dietary") or "").strip()
        reg.accessibility = (request.form.get("accessibility") or "").strip()
        reg.status = "pending"
        if not existing:
            db.session.add(reg)
        db.session.commit()
        audit.record("registration.saved",
                     target_kind="registration", target_id=reg.id,
                     summary=f"{current_user.email} → {c.slug} ({tier.name})")
        flash("Registration saved. You'll receive a payment link by email.",
              "success")
        return redirect(url_for("member.dashboard"))

    return render_template("member/register_conference.html",
                           c=c, tiers=tiers, existing=existing)


# ---------------------------------------------------------------------------
# Abstract submission
# ---------------------------------------------------------------------------

@member_bp.route("/conferences/<slug>/abstract", methods=["GET", "POST"])
@login_required
def submit_abstract(slug):
    c = (Conference.query
         .filter_by(slug=slug)
         .filter(Conference.deleted_at.is_(None))
         .first_or_404())
    if c.is_draft:
        abort(404)
    if not c.accepts_abstracts and not c.external_abstract_url:
        flash("Abstract submission is not open for this conference.", "error")
        return redirect(url_for("public.conference_detail", slug=c.slug))
    tracks = c.tracks_list()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        authors = (request.form.get("authors") or "").strip()
        body = (request.form.get("body") or "").strip()
        track = (request.form.get("track") or "").strip()
        ptype = (request.form.get("presentation_type") or "Either").strip()
        keywords = (request.form.get("keywords") or "").strip()
        coi = (request.form.get("coi") or "").strip()

        words = len(body.split())
        if not (title and authors and body):
            flash("Title, authors and abstract body are required.", "error")
        elif words > 320:
            flash(
                f"Abstract body is {words} words — the limit is 300 (soft cap 320).",
                "error",
            )
        else:
            a = Abstract(
                user_id=current_user.id, conference_id=c.id,
                title=title, authors=authors, body=body, track=track,
                presentation_type=ptype, keywords=keywords, coi=coi,
            )
            f = request.files.get("figure")
            if f and f.filename:
                try:
                    a.figure_filename = save_figure(
                        f,
                        upload_folder=current_app.config["UPLOAD_FOLDER"],
                        max_bytes=current_app.config["MAX_FIGURE_BYTES"],
                    )
                except UploadError as e:
                    flash(str(e), "error")
                    return render_template("member/submit_abstract.html",
                                           c=c, tracks=tracks, form=request.form)
            db.session.add(a)
            db.session.commit()
            audit.record("abstract.submitted",
                         target_kind="abstract", target_id=a.id,
                         summary=f"{current_user.email} → {c.slug}: {title}")
            flash("Abstract submitted. You'll be notified after review.",
                  "success")
            return redirect(url_for("member.dashboard"))

    return render_template("member/submit_abstract.html",
                           c=c, tracks=tracks, form={})


# ---------------------------------------------------------------------------
# Author-only figure download
# ---------------------------------------------------------------------------

@member_bp.route("/abstracts/<int:aid>/figure")
@login_required
def abstract_figure(aid):
    a = Abstract.query.get_or_404(aid)
    # Author OR anyone with abstract review permission.
    if a.user_id != current_user.id and not current_user.has_permission("abs.review"):
        abort(403)
    if not a.figure_filename:
        abort(404)
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "abstracts"
    name = a.figure_filename.split("/", 1)[-1]
    return send_from_directory(folder, name)
