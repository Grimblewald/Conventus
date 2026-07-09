"""Member-area routes: dashboard, profile, conference registration, abstract
submission. All require login.
"""
from __future__ import annotations

import re
import secrets
from pathlib import Path
from datetime import datetime, timedelta

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template,
    request, send_from_directory, url_for,
)
from flask_login import current_user, login_required

from ...extensions import db
from ...models import Abstract, Conference, OTPCode, Registration, ReviewAssignment
from ...models.content import get_site_settings
from ...security import audit
from ...services.mail import send_mail
from ...services.payments import payment_url_for, send_payment_email
from ...services.uploads import UploadError, save_figure, save_image
from ...services.form_renderer import validate_form
from ...services.citations import fetch_metadata, format_reference


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
        .options(db.joinedload(Abstract.registration))
        .order_by(Abstract.created_at.desc())
        .all()
    )
    my_reviews = (
        ReviewAssignment.query
        .filter_by(reviewer_id=current_user.id)
        .filter(ReviewAssignment.status != "declined")
        .options(db.joinedload(ReviewAssignment.abstract))
        .order_by(ReviewAssignment.created_at.desc())
        .all()
    )
    return render_template("member/dashboard.html",
                           regs=regs, abstracts=abs_, my_reviews=my_reviews)


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

_DOI_URL_PREFIXES = [
    "https://doi.org/", "http://doi.org/",
    "https://dx.doi.org/", "http://dx.doi.org/",
    "doi.org/", "dx.doi.org/",
]


def _normalize_doi(raw: str) -> str:
    doi = raw.strip()
    for prefix in _DOI_URL_PREFIXES:
        if doi.lower().startswith(prefix.lower()):
            doi = doi[len(prefix):]
            break
    return doi.strip()


def _validate_reference(key: int, doi: str, body: str) -> list[str]:
    errors: list[str] = []
    marker = f"[{key}]"
    if marker not in body:
        errors.append(
            f"Reference {marker} ({doi}) is not cited in the abstract text. "
            f"Add {marker} where this reference belongs.")
    if not doi.startswith("10."):
        errors.append(f"Reference {marker} DOI does not look valid (should start with 10.).")
    return errors


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

    # Enforce per-user abstract limit (exclude drafts, skip when editing)
    edit_id = request.args.get("edit", type=int) or request.form.get("edit_id", type=int)
    if c.max_abstracts_per_user and not edit_id:
        existing_count = (
            Abstract.query
            .filter_by(user_id=current_user.id, conference_id=c.id)
            .filter(Abstract.deleted_at.is_(None))
            .filter(Abstract.status != "draft")
            .count()
        )
        if existing_count >= c.max_abstracts_per_user:
            flash(
                f"You've reached the limit of {c.max_abstracts_per_user} "
                f"abstract(s) for this conference.", "error",
            )
            return redirect(url_for("public.conference_detail", slug=c.slug))

    tracks = c.tracks_list()
    abstract_schema = c.abstract_form_schema

    # Edit mode — load existing abstract
    draft = None
    if edit_id:
        draft = (Abstract.query
                 .filter_by(id=edit_id, user_id=current_user.id)
                 .filter(Abstract.deleted_at.is_(None))
                 .first())

    if request.method == "POST":
        action = request.form.get("action", "submit")
        is_draft = action in ("draft", "preview")

        title = (request.form.get("title") or "").strip()
        authors = (request.form.get("authors") or "").strip()
        body = (request.form.get("body") or "").strip()
        track = (request.form.get("track") or "").strip()
        ptype = (request.form.get("presentation_type") or "Either").strip()
        keywords = (request.form.get("keywords") or "").strip()
        coi = (request.form.get("coi") or "").strip()

        # Collect custom field data
        custom_data: dict = {}
        if abstract_schema:
            for section in abstract_schema.get("sections", []):
                for field in section.get("fields", []):
                    key = field.get("key", "")
                    val = request.form.getlist(key) if field.get("type") == "checkbox-group" else request.form.get(key, "")
                    if val:
                        custom_data[key] = val

        # Collect references
        ref_dois = request.form.getlist("ref_doi[]")
        references = []
        ref_keys = set()
        seen_dois = set()
        for i, doi in enumerate(ref_dois):
            doi = _normalize_doi(doi)
            if doi and doi not in seen_dois:
                key = len(references) + 1
                references.append({"key": key, "doi": doi})
                ref_keys.add(key)
                seen_dois.add(doi)

        presenting_author_index = 0
        try:
            presenting_author_index = int(
                request.form.get("presenting_author_index", "0") or "0")
        except ValueError:
            pass

        errors: list[str] = []

        if not is_draft:
            # Full validation for submit
            if not (title and authors and body):
                errors.append("Title, authors and abstract body are required.")
            if len(title.split()) > 15:
                errors.append(f"Title is {len(title.split())} words — the limit is 15.")
            if len(body.split()) > 320:
                errors.append(f"Abstract body is {len(body.split())} words — the limit is 300 (soft cap 320).")

            if abstract_schema:
                form_errors = validate_form(abstract_schema, request.form)
                errors.extend(form_errors)

            # Reference validation
            ref_errors: list[str] = []
            for ref in references:
                ref_errors.extend(_validate_reference(ref["key"], ref["doi"], body))
            body_markers = re.findall(r"\[(\d+)\]", body)
            for m in body_markers:
                n = int(m)
                if n not in ref_keys:
                    ref_errors.append(
                        f"Citation [\u200B{n}\u200B] appears in text but has no matching reference.")
            errors.extend(ref_errors)

        elif not (title and authors):
            errors.append("Title and at least one author are required even for drafts.")

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("member/submit_abstract.html",
                                   c=c, tracks=tracks, form=request.form,
                                   abstract_schema=abstract_schema, draft=draft)

        # Save abstract
        if draft:
            a = draft
        else:
            a = Abstract(user_id=current_user.id, conference_id=c.id)
        a.title = title
        a.authors = authors
        a.body = body
        a.track = track
        a.presentation_type = ptype
        a.keywords = keywords
        a.coi = coi
        a.custom_data = custom_data if custom_data else None
        a.presenting_author_index = presenting_author_index
        a.references = references if references else None
        if not is_draft:
            a.status = "submitted"
        elif not draft:
            a.status = "draft"
        # else: keep existing status (e.g. "revise") on draft saves

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
                                       abstract_schema=abstract_schema, draft=draft)

        pic = request.files.get("profile_picture")
        if pic and pic.filename:
            try:
                rel = save_image(pic,
                                 upload_folder=current_app.config["UPLOAD_FOLDER"],
                                 subdir="abstracts", prefix="profile-",
                                 max_bytes=current_app.config["MAX_HERO_BYTES"],
                                 target_size=400, force_webp=True)
                a.profile_picture_filename = rel.split("/", 1)[-1]
            except UploadError as e:
                flash(str(e), "error")
                return render_template("member/submit_abstract.html",
                                       c=c, tracks=tracks, form=request.form,
                                       abstract_schema=abstract_schema, draft=draft)

        if not draft:
            db.session.add(a)
        db.session.commit()

        # Auto-link abstract to an existing registration for this conference.
        if a.registration_id is None:
            reg = Registration.query.filter_by(
                user_id=current_user.id,
                conference_id=c.id,
                deleted_at=None,
            ).first()
            if reg:
                a.registration_id = reg.id
                db.session.commit()

        if action == "preview":
            return redirect(url_for("member.preview_abstract", aid=a.id))

        audit.record("abstract.submitted" if not is_draft else "abstract.draft",
                     target_kind="abstract", target_id=a.id,
                     summary=f"{current_user.email} → {c.slug}: {title}")
        flash("Abstract submitted. You'll be notified after review." if not is_draft
              else "Draft saved.", "success")
        return redirect(url_for("member.dashboard"))

    # GET — pre-fill form for editing
    form_data: dict = {}
    if draft:
        form_data = {
            "title": draft.title,
            "authors": draft.authors,
            "body": draft.body,
            "track": draft.track,
            "presentation_type": draft.presentation_type,
            "keywords": draft.keywords,
            "coi": draft.coi,
            "presenting_author_index": draft.presenting_author_index,
            **{k: v for k, v in (draft.custom_data or {}).items()},
        }

    return render_template("member/submit_abstract.html",
                           c=c, tracks=tracks, form=form_data, draft=draft,
                           abstract_schema=abstract_schema)


# ---------------------------------------------------------------------------
# Abstract preview (fetches DOI metadata for references)
# ---------------------------------------------------------------------------

@member_bp.route("/abstracts/<int:aid>/preview")
@login_required
def preview_abstract(aid):
    a = Abstract.query.get_or_404(aid)
    if a.user_id != current_user.id:
        abort(403)

    refs_with_meta: list[dict] = []
    for ref in (a.references or []):
        doi = ref["doi"]
        meta = fetch_metadata(doi)
        if meta:
            refs_with_meta.append({
                "key": ref["key"],
                "doi": doi,
                "citation": format_reference(meta),
            })
        else:
            refs_with_meta.append({
                "key": ref["key"],
                "doi": doi,
                "citation": doi,
            })

    return render_template("member/preview_abstract.html",
                           a=a, refs_with_meta=refs_with_meta)


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
# Review form — reviewers score and comment on assigned abstracts
# ---------------------------------------------------------------------------

@member_bp.route("/review/<int:assignment_id>", methods=["GET", "POST"])
@login_required
def review_form(assignment_id):
    ra = (ReviewAssignment.query
          .options(db.joinedload(ReviewAssignment.abstract))
          .get_or_404(assignment_id))
    if ra.reviewer_id != current_user.id:
        abort(403)

    a = ra.abstract
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        ra.score = int(request.form.get("score", 0))
        ra.recommendation = (request.form.get("recommendation") or "").strip() or None
        ra.comments_author = (request.form.get("comments_author") or "").strip()
        ra.comments_chair = (request.form.get("comments_chair") or "").strip()

        if action == "submit":
            if ra.score is None or ra.score < 0 or ra.score > 100:
                flash("Please provide a score between 0 and 100.", "error")
                return render_template("member/review_form.html", ra=ra, a=a)
            if not ra.recommendation:
                flash("Please select a recommendation.", "error")
                return render_template("member/review_form.html", ra=ra, a=a)
            ra.status = "completed"
            ra.submitted_at = datetime.utcnow()
            db.session.commit()
            flash("Review submitted. Thank you.", "success")
            return redirect(url_for("member.dashboard"))
        else:
            ra.status = "pending"
            db.session.commit()
            flash("Draft saved.", "success")

    return render_template("member/review_form.html", ra=ra, a=a)


@member_bp.route("/review/<int:assignment_id>/recuse", methods=["POST"])
@login_required
def review_recuse(assignment_id):
    ra = ReviewAssignment.query.get_or_404(assignment_id)
    if ra.reviewer_id != current_user.id:
        abort(403)
    if ra.status != "pending":
        flash("You can only recuse from a pending review.", "error")
        return redirect(url_for("member.review_form", assignment_id=ra.id))

    reason = (request.form.get("decline_reason") or "").strip()
    ra.decline_reason = reason
    ra.status = "declined"
    db.session.commit()
    flash("You have been removed from this review. Thank you for letting us know.", "success")
    return redirect(url_for("member.dashboard"))


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
