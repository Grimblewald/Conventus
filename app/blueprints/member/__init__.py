"""Member-area routes: dashboard, profile, conference registration, abstract
submission. All require login.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from datetime import datetime, timedelta

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template,
    request, send_from_directory, url_for,
)
from flask_login import current_user, login_required

from ...extensions import db
from ...models import Abstract, Conference, OTPCode, Registration
from ...models.content import get_site_settings
from ...security import audit
from ...services.mail import send_mail
from ...services.payments import payment_url_for, send_payment_email
from ...services.uploads import UploadError, save_figure, save_image
from ...services.form_renderer import validate_form


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
    schema = c.registration_form_schema
    sub_events = list(c.sub_events)

    if request.method == "POST":
        tier_name = (request.form.get("tier") or "").strip()
        tier = next((t for t in tiers if t.name == tier_name), None)
        if not tier:
            flash("Please choose a registration tier.", "error")
            return render_template("member/register_conference.html",
                                   c=c, tiers=tiers, existing=existing,
                                   schema=schema, sub_events=sub_events)

        # Collect custom field data from schema
        custom_data: dict = {}
        if schema:
            for section in schema.get("sections", []):
                for field in section.get("fields", []):
                    key = field.get("key", "")
                    val = request.form.getlist(key) if field.get("type") == "checkbox-group" else request.form.get(key, "")
                    custom_data[key] = val

        # Collect sub-event registration data
        sub_event_data: dict = {}
        for se in sub_events:
            sekey = se.name.lower().replace(" ", "_")
            attending = request.form.get(f"_sub_event_{sekey}_attending") == "yes"
            entry = {"attending": attending}
            if attending and se.preference_schema:
                for pf in se.preference_schema.get("fields", []):
                    pfkey = pf.get("key", "")
                    pval = request.form.getlist(f"_sub_event_{sekey}_{pfkey}") if pf.get("type") == "checkbox-group" else request.form.get(f"_sub_event_{sekey}_{pfkey}", "")
                    entry[pfkey] = pval
            sub_event_data[sekey] = entry

        # Validate custom fields against schema
        if schema:
            form_errors = validate_form(schema, request.form)
            if form_errors:
                for err in form_errors:
                    flash(err, "error")
                return render_template("member/register_conference.html",
                                       c=c, tiers=tiers, existing=existing,
                                       schema=schema, sub_events=sub_events)

        reg = existing or Registration(user_id=current_user.id, conference_id=c.id)
        reg.tier_name = tier.name
        amount = tier.early_bird_amount if tier.early_bird_amount and c.early_bird_deadline and c.early_bird_deadline >= datetime.utcnow().date() else tier.amount
        reg.amount = amount
        reg.dietary = (request.form.get("dietary") or "").strip()
        reg.accessibility = (request.form.get("accessibility") or "").strip()
        reg.custom_data = custom_data if custom_data else None
        reg.sub_events = sub_event_data if any(v.get("attending") for v in sub_event_data.values()) else None
        reg.status = "pending"
        if not existing:
            db.session.add(reg)
        db.session.commit()
        audit.record("registration.saved",
                     target_kind="registration", target_id=reg.id,
                     summary=f"{current_user.email} → {c.slug} ({tier.name})")

        site = get_site_settings()
        if site.payment_portal_enabled:
            pay_url = payment_url_for(reg)
            send_payment_email(reg, pay_url)
            reg.payment_sent_at = datetime.utcnow()
            db.session.commit()
            flash("Registration saved. A payment link has been emailed to you.",
                  "success")
        else:
            flash("Registration saved. Our payment portal is under construction — "
                  "you will be notified when it is ready.", "warning")
        return redirect(url_for("member.dashboard"))

    return render_template("member/register_conference.html",
                           c=c, tiers=tiers, existing=existing,
                           schema=schema, sub_events=sub_events)


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

    # Enforce per-user abstract limit
    if c.max_abstracts_per_user:
        existing = (
            Abstract.query
            .filter_by(user_id=current_user.id, conference_id=c.id)
            .filter(Abstract.deleted_at.is_(None))
            .count()
        )
        if existing >= c.max_abstracts_per_user:
            flash(
                f"You've reached the limit of {c.max_abstracts_per_user} "
                f"abstract(s) for this conference.", "error",
            )
            return redirect(url_for("public.conference_detail", slug=c.slug))

    tracks = c.tracks_list()
    abstract_schema = c.abstract_form_schema

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        authors = (request.form.get("authors") or "").strip()
        body = (request.form.get("body") or "").strip()
        track = (request.form.get("track") or "").strip()
        ptype = (request.form.get("presentation_type") or "Either").strip()
        keywords = (request.form.get("keywords") or "").strip()
        coi = (request.form.get("coi") or "").strip()

        # Collect custom field data from abstract schema
        custom_data: dict = {}
        if abstract_schema:
            for section in abstract_schema.get("sections", []):
                for field in section.get("fields", []):
                    key = field.get("key", "")
                    val = request.form.getlist(key) if field.get("type") == "checkbox-group" else request.form.get(key, "")
                    if val:
                        custom_data[key] = val

        words = len(body.split())
        if not (title and authors and body):
            flash("Title, authors and abstract body are required.", "error")
        elif words > 320:
            flash(
                f"Abstract body is {words} words — the limit is 300 (soft cap 320).",
                "error",
            )
        elif abstract_schema:
            form_errors = validate_form(abstract_schema, request.form)
            if form_errors:
                for err in form_errors:
                    flash(err, "error")
        else:
            a = Abstract(
                user_id=current_user.id, conference_id=c.id,
                title=title, authors=authors, body=body, track=track,
                presentation_type=ptype, keywords=keywords, coi=coi,
                custom_data=custom_data if custom_data else None,
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
                                           c=c, tracks=tracks, form=request.form,
                                           abstract_schema=abstract_schema)

            pic = request.files.get("profile_picture")
            if pic and pic.filename:
                try:
                    rel = save_image(
                        pic,
                        upload_folder=current_app.config["UPLOAD_FOLDER"],
                        subdir="abstracts",
                        prefix="profile-",
                        max_bytes=current_app.config["MAX_HERO_BYTES"],
                        target_size=400,
                        force_webp=True,
                    )
                    a.profile_picture_filename = rel.split("/", 1)[-1]
                except UploadError as e:
                    flash(str(e), "error")
                    return render_template("member/submit_abstract.html",
                                           c=c, tracks=tracks, form=request.form,
                                           abstract_schema=abstract_schema)
            db.session.add(a)
            db.session.commit()
            audit.record("abstract.submitted",
                         target_kind="abstract", target_id=a.id,
                         summary=f"{current_user.email} → {c.slug}: {title}")
            flash("Abstract submitted. You'll be notified after review.",
                  "success")
            return redirect(url_for("member.dashboard"))

    return render_template("member/submit_abstract.html",
                           c=c, tracks=tracks, form={},
                           abstract_schema=abstract_schema)


# ---------------------------------------------------------------------------
# Abstract soft-delete (OTP-confirmed, member)
# ---------------------------------------------------------------------------

@member_bp.route("/abstracts/<int:aid>/delete-request", methods=["POST"])
@login_required
def delete_abstract_request(aid):
    a = Abstract.query.get_or_404(aid)
    if a.user_id != current_user.id:
        abort(403)
    if a.deleted_at is not None:
        flash("This abstract has already been deleted.", "error")
        return redirect(url_for("member.dashboard"))
    if a.status not in ("submitted", "accepted"):
        flash("This abstract can no longer be deleted.", "error")
        return redirect(url_for("member.dashboard"))
    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    ok = send_mail(
        to=current_user.email,
        subject="Confirm abstract deletion",
        body=(f"You requested to delete the abstract \"{a.title}\".\n\n"
              f"Confirmation code: {code}\n\n"
              f"This code expires in {ttl // 60} minutes. "
              f"If you didn't request this, ignore the email."),
    )
    if not ok:
        flash("Failed to send confirmation email. Please try again.", "error")
        return redirect(url_for("member.dashboard"))
    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="abstract_delete",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    flash("A confirmation code has been sent to your email.", "success")
    return redirect(url_for("member.delete_abstract_confirm", aid=a.id))


@member_bp.route("/abstracts/<int:aid>/delete-confirm", methods=["GET", "POST"])
@login_required
def delete_abstract_confirm(aid):
    a = Abstract.query.get_or_404(aid)
    if a.user_id != current_user.id:
        abort(403)
    if a.deleted_at is not None:
        flash("This abstract has already been deleted.", "error")
        return redirect(url_for("member.dashboard"))
    if a.status not in ("submitted", "accepted"):
        flash("This abstract can no longer be deleted.", "error")
        return redirect(url_for("member.dashboard"))
    if request.method == "POST":
        entered = (request.form.get("code") or "").strip().replace(" ", "")
        otp = (OTPCode.query
               .filter_by(email=current_user.email.lower(),
                          code=entered,
                          purpose="abstract_delete",
                          consumed_at=None)
               .order_by(OTPCode.id.desc())
               .first())
        if not (otp and otp.is_valid()):
            flash("That code didn't match, or it has expired.", "error")
            return render_template("member/abstract_delete_confirm.html", a=a)
        otp.consumed_at = datetime.utcnow()
        title = a.title
        a.deleted_at = datetime.utcnow()
        db.session.commit()
        audit.record("abstract.deleted",
                     target_kind="abstract", target_id=a.id,
                     summary=f"{current_user.email} deleted \"{title}\"")
        flash(f"Deleted abstract \"{title}\".", "success")
        return redirect(url_for("member.dashboard"))
    return render_template("member/abstract_delete_confirm.html", a=a)


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


# ---------------------------------------------------------------------------
# Payment stub — replace with real payment provider integration.
# ---------------------------------------------------------------------------

@member_bp.route("/pay/<int:reg_id>")
@login_required
def pay_registration(reg_id):
    reg = Registration.query.get_or_404(reg_id)
    if reg.user_id != current_user.id:
        abort(403)
    if reg.status == "paid":
        flash("This registration is already paid.", "success")
        return redirect(url_for("member.dashboard"))
    site = get_site_settings()
    return render_template("member/pay.html", reg=reg, site=site)
