"""Admin → Conferences: CRUD with audit, soft delete via OTP, abstract
review, and per-conference price tiers.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import (
    current_app, flash, redirect, render_template, request, send_file, url_for,
)
from flask_login import current_user

from . import admin_bp
from ...extensions import db
from ...models import Abstract, Conference, ConferenceReviewer, OTPCode, OrganisingCommitteeMember, Registration, ReviewAssignment, Sponsor, SponsorTier, SubEvent, User
from ...models.abstract import ALL_STATUSES, SPEAKER_STATUSES
from ...models.conference import PriceTier
from ...security import requires_permission, audit
from ...services.jinja_filters import parse_cents
from ...services.mail import send_mail
from ...services.slugs import slugify
from ...services.uploads import (
    UploadError, save_image, save_pdf, save_figure, remove_upload,
)
from ...services.citations import fetch_metadata, format_reference_compact, normalize_doi


def _tier_price(raw: str | None, current: int | None) -> int | None:
    """Parse a sponsor-tier price field into cents.

    Blank means "this level has no set price" (NULL), which is different from
    zero — a free tier is a real thing and must survive a save. Unparseable
    input keeps the current value rather than silently zeroing what a sponsor
    is billed.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return parse_cents(text)
    except ValueError:
        return current


# ---------------------------------------------------------------------------
# List + create
# ---------------------------------------------------------------------------

@admin_bp.route("/conferences", methods=["GET", "POST"])
@requires_permission("conf.create", "conf.edit", "conf.delete")
def conferences():
    if request.method == "POST" and current_user.has_permission("conf.create"):
        try:
            new_slug = slugify(request.form.get("slug", ""))
            if not new_slug:
                raise ValueError("Slug is empty after sanitisation.")
            if _slug_taken(new_slug):
                flash(f"Slug “{new_slug}” is already in use.", "error")
                return redirect(url_for("admin.conferences"))
            c = Conference(
                slug=new_slug,
                title=(request.form.get("title") or "").strip(),
                subtitle=(request.form.get("subtitle") or "").strip(),
                summary=(request.form.get("summary") or "").strip(),
                body=(request.form.get("body") or "").strip(),
                start_date=date.fromisoformat(request.form["start_date"]),
                end_date=date.fromisoformat(request.form["end_date"]),
                city=(request.form.get("city") or "").strip(),
                venue=(request.form.get("venue") or "").strip(),
                tracks=request.form.get("tracks", ""),
                is_featured=bool(request.form.get("is_featured")),
                is_draft=bool(request.form.get("is_draft")),
                abstract_deadline=(
                    date.fromisoformat(request.form["abstract_deadline"])
                    if request.form.get("abstract_deadline") else None),
                early_bird_deadline=(
                    date.fromisoformat(request.form["early_bird_deadline"])
                    if request.form.get("early_bird_deadline") else None),
                registration_deadline=(
                    date.fromisoformat(request.form["registration_deadline"])
                    if request.form.get("registration_deadline") else None),
                is_accepting_abstracts=not bool(request.form.get("not_accepting_abstracts")),
                is_accepting_registrations=not bool(request.form.get("not_accepting_registrations")),
                abstracts_reopen_date=(
                    date.fromisoformat(request.form["abstracts_reopen_date"])
                    if request.form.get("abstracts_reopen_date") else None),
                registrations_reopen_date=(
                    date.fromisoformat(request.form["registrations_reopen_date"])
                    if request.form.get("registrations_reopen_date") else None),
                external_registration_url=(request.form.get("external_registration_url") or "").strip() or None,
                external_abstract_url=(request.form.get("external_abstract_url") or "").strip() or None,
            )
            db.session.add(c)
            if c.is_featured:
                _unfeature_others()
            db.session.commit()
            audit.record("conference.created",
                         target_kind="conference", target_id=c.id,
                         summary=f"Created “{c.title}”")
            flash("Conference created.", "success")
        except Exception as e:
            flash(f"Could not create conference: {e}", "error")
        return redirect(url_for("admin.conferences"))

    items = (
        Conference.query
        .filter(Conference.deleted_at.is_(None))
        .order_by(Conference.start_date.desc())
        .all()
    )
    return render_template("admin/conferences.html", items=items)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

_DATE_FIELDS = (
    "start_date", "end_date",
    "abstract_deadline", "early_bird_deadline", "registration_deadline",
    "abstracts_reopen_date", "registrations_reopen_date",
    "review_deadline",
)


@admin_bp.route("/conferences/<int:cid>/edit", methods=["GET"])
@requires_permission("conf.edit")
def conference_edit(cid):
    c = Conference.query.get_or_404(cid)
    return render_template("admin/conference_edit.html", c=c)


# ---------------------------------------------------------------------------
# Unified save (handles conference fields, price tiers, sponsor tiers, sponsors)
# ---------------------------------------------------------------------------

@admin_bp.route("/conferences/<int:cid>/save", methods=["POST"])
@requires_permission("conf.edit")
def conference_save(cid):
    c = Conference.query.get_or_404(cid)
    try:
        # -- Conference fields --
        new_slug = slugify(request.form.get("slug", ""))
        if not new_slug:
            raise ValueError("Slug is empty after sanitisation.")
        if _slug_taken(new_slug, exclude_id=c.id):
            raise ValueError(f"Slug \"{new_slug}\" is already used by another conference.")
        c.slug = new_slug
        c.title = (request.form.get("title") or "").strip()
        c.subtitle = (request.form.get("subtitle") or "").strip()
        c.summary = (request.form.get("summary") or "").strip()
        c.body = (request.form.get("body") or "").strip()
        c.city = (request.form.get("city") or "").strip()
        c.venue = (request.form.get("venue") or "").strip()
        c.tracks = request.form.get("tracks", "")
        c.hero_caption = (request.form.get("hero_caption") or "").strip()
        c.hero_image_mode = request.form.get("hero_image_mode", "cover").strip() or "cover"
        c.is_featured = bool(request.form.get("is_featured"))
        if c.is_featured:
            _unfeature_others(c.id)
        c.is_draft = bool(request.form.get("is_draft"))
        c.is_accepting_abstracts = bool(request.form.get("is_accepting_abstracts"))
        c.is_accepting_registrations = bool(request.form.get("is_accepting_registrations"))
        c.external_registration_url = (request.form.get("external_registration_url") or "").strip() or None
        c.external_abstract_url = (request.form.get("external_abstract_url") or "").strip() or None
        raw_max = (request.form.get("max_abstracts_per_user") or "").strip()
        c.max_abstracts_per_user = int(raw_max) if raw_max else None
        raw_rpp = (request.form.get("reviewers_per_paper") or "").strip()
        c.reviewers_per_paper = int(raw_rpp) if raw_rpp else 2

        for fld in _DATE_FIELDS:
            raw = (request.form.get(fld) or "").strip()
            setattr(c, fld, date.fromisoformat(raw) if raw else None)

        # Hero image
        f = request.files.get("hero_image")
        if f and f.filename:
            rel = save_image(
                f, upload_folder=current_app.config["UPLOAD_FOLDER"],
                subdir="conferences", prefix=f"hero-c{c.id}",
                max_bytes=current_app.config["MAX_HERO_BYTES"], target_size=1920,
            )
            if c.hero_image_filename:
                remove_upload(current_app.config["UPLOAD_FOLDER"],
                              f"conferences/{c.hero_image_filename}")
            c.hero_image_filename = rel.split("/", 1)[-1]
        elif request.form.get("remove_hero_image"):
            if c.hero_image_filename:
                remove_upload(current_app.config["UPLOAD_FOLDER"],
                              f"conferences/{c.hero_image_filename}")
            c.hero_image_filename = None

        # Booklet PDF
        pdf = request.files.get("booklet")
        if pdf and pdf.filename:
            rel = save_pdf(
                pdf, upload_folder=current_app.config["UPLOAD_FOLDER"],
                subdir="conferences", prefix=f"booklet-c{c.id}",
                max_bytes=current_app.config["MAX_BOOKLET_BYTES"],
            )
            if c.booklet_filename:
                remove_upload(current_app.config["UPLOAD_FOLDER"],
                              f"conferences/{c.booklet_filename}")
            c.booklet_filename = rel.split("/", 1)[-1]
        elif request.form.get("remove_booklet"):
            if c.booklet_filename:
                remove_upload(current_app.config["UPLOAD_FOLDER"],
                              f"conferences/{c.booklet_filename}")
            c.booklet_filename = None

        # Booklet decoration images (header, footer, background)
        _BOOKLET_IMG_FIELDS = (
            ("booklet_header", "booklet_header_filename", "booklet-header"),
            ("booklet_footer", "booklet_footer_filename", "booklet-footer"),
            ("booklet_background", "booklet_background_filename", "booklet-bg"),
        )
        for form_name, col_name, prefix in _BOOKLET_IMG_FIELDS:
            uf = request.files.get(form_name)
            if uf and uf.filename:
                rel = save_image(
                    uf, upload_folder=current_app.config["UPLOAD_FOLDER"],
                    subdir="conferences", prefix=f"{prefix}-c{c.id}",
                    max_bytes=current_app.config["MAX_HERO_BYTES"],
                )
                old_val = getattr(c, col_name, None)
                if old_val:
                    remove_upload(current_app.config["UPLOAD_FOLDER"],
                                  f"conferences/{old_val}")
                setattr(c, col_name, rel.split("/", 1)[-1])
            elif request.form.get(f"remove_{form_name}"):
                old_val = getattr(c, col_name, None)
                if old_val:
                    remove_upload(current_app.config["UPLOAD_FOLDER"],
                                  f"conferences/{old_val}")
                setattr(c, col_name, None)

        # -- Price tiers --
        for t in list(c.price_tiers):
            if request.form.get(f"tier_delete_{t.id}"):
                db.session.delete(t)
                continue
            if f"tier_name_{t.id}" in request.form:
                t.name = (request.form.get(f"tier_name_{t.id}") or "").strip()
            if f"tier_amount_{t.id}" in request.form:
                try:
                    t.amount = parse_cents(request.form.get(f"tier_amount_{t.id}") or "0")
                except ValueError:
                    pass
            if f"tier_desc_{t.id}" in request.form:
                t.description = (request.form.get(f"tier_desc_{t.id}") or "").strip()
            if f"tier_order_{t.id}" in request.form:
                try:
                    t.display_order = int(request.form.get(f"tier_order_{t.id}") or 0)
                except ValueError:
                    pass
            if f"tier_eb_amt_{t.id}" in request.form:
                if request.form.get(f"tier_eb_{t.id}"):
                    try:
                        t.early_bird_amount = parse_cents(request.form.get(f"tier_eb_amt_{t.id}") or "0")
                    except ValueError:
                        pass
                else:
                    t.early_bird_amount = None

        # Add new price tiers
        new_names = request.form.getlist("new_tier_name[]")
        new_amounts = request.form.getlist("new_tier_amount[]")
        new_eb_amounts = request.form.getlist("new_tier_eb_amt[]")
        new_descs = request.form.getlist("new_tier_desc[]")
        new_orders = request.form.getlist("new_tier_order[]")
        for i, name in enumerate(new_names):
            name = name.strip()
            if not name:
                continue
            amount = 0
            try:
                amount = parse_cents(new_amounts[i] or "0")
            except (IndexError, ValueError):
                pass
            desc = (new_descs[i] if i < len(new_descs) else "").strip()
            order = 0
            try:
                order = int(new_orders[i] or 0)
            except (IndexError, ValueError):
                pass
            eb_amt = None
            try:
                raw_eb = (new_eb_amounts[i] if i < len(new_eb_amounts) else "0")
                eb_amt = parse_cents(raw_eb) if raw_eb else None
            except (IndexError, ValueError):
                pass
            db.session.add(PriceTier(
                conference_id=c.id,
                name=name,
                amount=amount,
                early_bird_amount=eb_amt,
                description=desc,
                display_order=order,
            ))

        # -- Sponsor tiers + sponsors --
        for st in list(c.sponsor_tiers):
            if request.form.get(f"stier_delete_{st.id}"):
                for s in st.sponsors:
                    if s.logo_filename:
                        remove_upload(current_app.config["UPLOAD_FOLDER"], f"sponsors/{s.logo_filename}")
                db.session.delete(st)
                continue
            if f"stier_name_{st.id}" in request.form:
                st.name = (request.form.get(f"stier_name_{st.id}") or "").strip()
            if f"stier_order_{st.id}" in request.form:
                try:
                    st.display_order = int(request.form.get(f"stier_order_{st.id}") or st.display_order)
                except ValueError:
                    pass
            if f"stier_price_{st.id}" in request.form:
                st.price = _tier_price(request.form.get(f"stier_price_{st.id}"), st.price)

            # Sponsors within this tier
            for s in list(st.sponsors):
                if request.form.get(f"sponsor_delete_{s.id}"):
                    if s.logo_filename:
                        remove_upload(current_app.config["UPLOAD_FOLDER"], f"sponsors/{s.logo_filename}")
                    db.session.delete(s)
                    continue
                if f"sponsor_name_{s.id}" in request.form:
                    s.name = (request.form.get(f"sponsor_name_{s.id}") or "").strip()
                if f"sponsor_url_{s.id}" in request.form:
                    s.url = (request.form.get(f"sponsor_url_{s.id}") or "").strip() or None
                if f"sponsor_order_{s.id}" in request.form:
                    try:
                        s.display_order = int(request.form.get(f"sponsor_order_{s.id}") or s.display_order)
                    except ValueError:
                        pass

            # Add new sponsors under this tier
            for i in range(50):
                sname = (request.form.get(f"new_sponsor_name_{st.id}_{i}") or "").strip()
                if not sname:
                    continue
                ns = Sponsor(
                    tier_id=st.id,
                    name=sname,
                    url=(request.form.get(f"new_sponsor_url_{st.id}_{i}") or "").strip() or None,
                    display_order=0,
                )
                try:
                    ns.display_order = int(request.form.get(f"new_sponsor_order_{st.id}_{i}") or 0)
                except ValueError:
                    pass
                logo = request.files.get(f"new_sponsor_logo_{st.id}_{i}")
                if logo and logo.filename:
                    try:
                        rel = save_image(
                            logo, upload_folder=current_app.config["UPLOAD_FOLDER"],
                            subdir="sponsors", prefix=f"sponsor-{c.id}",
                            max_bytes=current_app.config["MAX_HERO_BYTES"],
                            target_size=400,
                        )
                        ns.logo_filename = rel.split("/", 1)[-1]
                    except UploadError as e:
                        flash(f"Sponsor logo error: {e}", "error")
                db.session.add(ns)

        # Add new sponsor tiers
        new_st_names = request.form.getlist("new_stier_name[]")
        new_st_orders = request.form.getlist("new_stier_order[]")
        new_st_prices = request.form.getlist("new_stier_price[]")
        for i, name in enumerate(new_st_names):
            name = name.strip()
            if not name:
                continue
            order = 0
            try:
                order = int(new_st_orders[i] or 0)
            except (IndexError, ValueError):
                pass
            try:
                price = _tier_price(new_st_prices[i], None)
            except IndexError:
                price = None
            db.session.add(SponsorTier(
                conference_id=c.id,
                name=name,
                display_order=order,
                price=price,
            ))

        # -- Organising committee --
        for oc in list(c.organising_committee):
            if request.form.get(f"oc_delete_{oc.id}"):
                db.session.delete(oc)
                continue
            if f"oc_name_{oc.id}" in request.form:
                oc.full_name = (request.form.get(f"oc_name_{oc.id}") or "").strip()
            if f"oc_role_{oc.id}" in request.form:
                oc.role = (request.form.get(f"oc_role_{oc.id}") or "").strip()
            if f"oc_affil_{oc.id}" in request.form:
                oc.affiliation = (request.form.get(f"oc_affil_{oc.id}") or "").strip()
            if f"oc_email_{oc.id}" in request.form:
                oc.email = (request.form.get(f"oc_email_{oc.id}") or "").strip()
            if f"oc_order_{oc.id}" in request.form:
                try:
                    oc.display_order = int(request.form.get(f"oc_order_{oc.id}") or 0)
                except ValueError:
                    pass
            portrait = request.files.get(f"oc_portrait_{oc.id}")
            if portrait and portrait.filename:
                try:
                    rel = save_image(
                        portrait,
                        upload_folder=current_app.config["UPLOAD_FOLDER"],
                        subdir="committee",
                        prefix=f"oc-{oc.id}",
                        max_bytes=current_app.config["MAX_HERO_BYTES"],
                        target_size=400,
                    )
                    if oc.portrait_filename:
                        remove_upload(current_app.config["UPLOAD_FOLDER"],
                                      f"committee/{oc.portrait_filename}")
                    oc.portrait_filename = rel.split("/", 1)[-1]
                except UploadError as e:
                    flash(f"Portrait error: {e}", "error")
            elif request.form.get(f"oc_remove_portrait_{oc.id}"):
                if oc.portrait_filename:
                    remove_upload(current_app.config["UPLOAD_FOLDER"],
                                  f"committee/{oc.portrait_filename}")
                oc.portrait_filename = None

        new_oc_names = request.form.getlist("new_oc_name[]")
        new_oc_roles = request.form.getlist("new_oc_role[]")
        new_oc_affils = request.form.getlist("new_oc_affil[]")
        new_oc_emails = request.form.getlist("new_oc_email[]")
        new_oc_orders = request.form.getlist("new_oc_order[]")
        for i, name in enumerate(new_oc_names):
            name = name.strip()
            if not name:
                continue
            oc = OrganisingCommitteeMember(
                conference_id=c.id,
                full_name=name,
                role=(new_oc_roles[i] if i < len(new_oc_roles) else "").strip(),
                affiliation=(new_oc_affils[i] if i < len(new_oc_affils) else "").strip(),
                email=(new_oc_emails[i] if i < len(new_oc_emails) else "").strip(),
                display_order=int(new_oc_orders[i] or 0) if i < len(new_oc_orders) else 0,
            )
            new_portrait = request.files.getlist("new_oc_portrait[]")
            portrait = new_portrait[i] if i < len(new_portrait) else None
            if portrait and portrait.filename:
                db.session.add(oc)
                db.session.flush()
                try:
                    rel = save_image(
                        portrait,
                        upload_folder=current_app.config["UPLOAD_FOLDER"],
                        subdir="committee",
                        prefix=f"oc-{oc.id}",
                        max_bytes=current_app.config["MAX_HERO_BYTES"],
                        target_size=400,
                    )
                    oc.portrait_filename = rel.split("/", 1)[-1]
                except UploadError as e:
                    flash(f"Portrait error: {e}", "error")
            db.session.add(oc)

        # -- Sub-events --
        for se in list(c.sub_events):
            if request.form.get(f"se_delete_{se.id}"):
                db.session.delete(se)
                continue
            if f"se_name_{se.id}" in request.form:
                se.name = (request.form.get(f"se_name_{se.id}") or "").strip()
            if f"se_desc_{se.id}" in request.form:
                se.description = (request.form.get(f"se_desc_{se.id}") or "").strip()
            if f"se_price_{se.id}" in request.form:
                try:
                    se.price = int(request.form.get(f"se_price_{se.id}") or 0)
                except ValueError:
                    pass
            if f"se_eligibility_{se.id}" in request.form:
                se.eligibility_note = (request.form.get(f"se_eligibility_{se.id}") or "").strip()
            if f"se_order_{se.id}" in request.form:
                try:
                    se.display_order = int(request.form.get(f"se_order_{se.id}") or 0)
                except ValueError:
                    pass
            pf_keys = request.form.getlist(f"se_pf_key_{se.id}[]")
            if pf_keys:
                pfs: list[dict] = []
                pf_labels = request.form.getlist(f"se_pf_label_{se.id}[]")
                pf_types = request.form.getlist(f"se_pf_type_{se.id}[]")
                pf_reqs = request.form.getlist(f"se_pf_req_{se.id}[]")
                pf_opts = request.form.getlist(f"se_pf_opts_{se.id}[]")
                for pfi, pfk in enumerate(pf_keys):
                    pfk = pfk.strip()
                    if not pfk:
                        continue
                    pf: dict = {
                        "key": pfk,
                        "label": (pf_labels[pfi].strip() if pfi < len(pf_labels) else pfk),
                        "type": (pf_types[pfi].strip() if pfi < len(pf_types) else "text"),
                        "required": str(pfi) in pf_reqs,
                    }
                    opts_raw = (pf_opts[pfi].strip() if pfi < len(pf_opts) else "")
                    if opts_raw:
                        pf["options"] = [o.strip() for o in opts_raw.split(",") if o.strip()]
                    pfs.append(pf)
                se.preference_schema = {"fields": pfs}

        new_se_names = request.form.getlist("new_se_name[]")
        new_se_descs = request.form.getlist("new_se_desc[]")
        new_se_prices = request.form.getlist("new_se_price[]")
        new_se_elig = request.form.getlist("new_se_eligibility[]")
        new_se_orders = request.form.getlist("new_se_order[]")
        for i, name in enumerate(new_se_names):
            name = name.strip()
            if not name:
                continue
            price = 0
            try:
                price = int(new_se_prices[i] or 0)
            except (IndexError, ValueError):
                pass
            db.session.add(SubEvent(
                conference_id=c.id,
                name=name,
                description=(new_se_descs[i] if i < len(new_se_descs) else "").strip(),
                price=price,
                eligibility_note=(new_se_elig[i] if i < len(new_se_elig) else "").strip(),
                display_order=int(new_se_orders[i] or 0) if i < len(new_se_orders) else 0,
            ))

        db.session.commit()
        audit.record("conference.updated",
                     target_kind="conference", target_id=c.id,
                     summary=f"Saved \"{c.title}\"")
        flash("All changes saved.", "success")
    except UploadError as e:
        flash(str(e), "error")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not save: {e}", "error")
    return redirect(url_for("admin.conference_edit", cid=c.id))


# ---------------------------------------------------------------------------
# Price tiers (one-screen editor inside conference_edit)
# ---------------------------------------------------------------------------

@admin_bp.route("/conferences/<int:cid>/tiers", methods=["POST"])
@requires_permission("conf.edit")
def conference_tiers(cid):
    c = Conference.query.get_or_404(cid)
    action = request.form.get("action")
    if action == "add":
        t = PriceTier(
            conference_id=c.id,
            name=(request.form.get("name") or "Tier").strip(),
            amount=int(request.form.get("amount") or 0),
            description=(request.form.get("description") or "").strip(),
            display_order=(len(c.price_tiers) + 1) * 10,
        )
        db.session.add(t)
        db.session.commit()
        audit.record("tier.added", target_kind="price_tier", target_id=t.id,
                     summary=f"+ {t.name} for {c.slug}")
        flash("Tier added.", "success")
    elif action == "save":
        for t in c.price_tiers:
            t.name = (request.form.get(f"name_{t.id}") or t.name).strip()
            try:
                t.amount = int(request.form.get(f"amount_{t.id}") or t.amount)
            except ValueError:
                pass
            t.description = (request.form.get(f"desc_{t.id}") or "").strip()
            try:
                t.display_order = int(request.form.get(f"order_{t.id}") or 0)
            except ValueError:
                pass
        db.session.commit()
        audit.record("tier.updated", target_kind="conference", target_id=c.id,
                     summary=f"Saved tiers for {c.slug}")
        flash("Tiers saved.", "success")
    elif action == "delete":
        try:
            t = PriceTier.query.get(int(request.form.get("tier_id", "")))
        except (TypeError, ValueError):
            t = None
        if t and t.conference_id == c.id:
            db.session.delete(t)
            db.session.commit()
            audit.record("tier.deleted", target_kind="price_tier",
                         target_id=t.id, summary=f"Deleted {t.name}")
            flash("Tier removed.", "success")
    return redirect(url_for("admin.conference_edit", cid=c.id))


# ---------------------------------------------------------------------------
# Delete (OTP-confirmed)
# ---------------------------------------------------------------------------

@admin_bp.route("/conferences/<int:cid>/delete-request", methods=["POST"])
@requires_permission("conf.delete")
def conference_delete_request(cid):
    c = Conference.query.get_or_404(cid)
    reg_count = (Registration.query.filter_by(conference_id=c.id)
                 .filter(Registration.deleted_at.is_(None)).count())
    abs_count = (Abstract.query.filter_by(conference_id=c.id)
                 .filter(Abstract.deleted_at.is_(None)).count())
    if reg_count or abs_count:
        flash(
            f"Cannot delete: {reg_count} registration(s) and {abs_count} "
            f"abstract(s) are attached. Soft-delete those first.",
            "error",
        )
        return redirect(url_for("admin.conference_edit", cid=c.id))

    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    ok = send_mail(
        to=current_user.email,
        subject="Confirm conference deletion",
        body=(f"You requested to delete the conference “{c.title}”.\n\n"
              f"Confirmation code: {code}\n\n"
              f"This code expires in {ttl // 60} minutes. "
              f"If you didn't request this, ignore the email."),
    )
    if not ok:
        flash("Failed to send confirmation email. Please try again.", "error")
        return redirect(url_for("admin.conference_edit", cid=c.id))
    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="conference_delete",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    flash("A confirmation code has been sent to your email.", "success")
    return redirect(url_for("admin.conference_delete_confirm", cid=c.id))


@admin_bp.route("/conferences/<int:cid>/delete-confirm", methods=["GET", "POST"])
@requires_permission("conf.delete")
def conference_delete_confirm(cid):
    c = Conference.query.get_or_404(cid)
    if request.method == "POST":
        entered = (request.form.get("code") or "").strip().replace(" ", "")
        otp = (OTPCode.query
               .filter_by(email=current_user.email.lower(),
                          code=entered,
                          purpose="conference_delete",
                          consumed_at=None)
               .order_by(OTPCode.id.desc())
               .first())
        if not (otp and otp.is_valid()):
            flash("That code didn't match, or it has expired.", "error")
            return render_template("admin/conference_delete_confirm.html", c=c)
        otp.consumed_at = datetime.utcnow()
        title = c.title
        c.deleted_at = datetime.utcnow()
        db.session.commit()
        audit.record("conference.deleted",
                     target_kind="conference", target_id=c.id,
                     summary=f"Deleted “{title}”")
        flash(f"Deleted conference “{title}”.", "success")
        return redirect(url_for("admin.conferences"))
    return render_template("admin/conference_delete_confirm.html", c=c)


# ---------------------------------------------------------------------------
# Abstract review (shared with committee permission `abs.review`)
# ---------------------------------------------------------------------------

@admin_bp.route("/abstracts")
@requires_permission("abs.review")
def abstracts():
    status = request.args.get("status", "submitted")
    search = (request.args.get("search") or "").strip()
    conf_id = request.args.get("conference_id", type=int)

    q = Abstract.query.filter(Abstract.deleted_at.is_(None))
    if status != "all":
        q = q.filter_by(status=status)
    if search:
        # Outer join: admin-entered abstracts have no author account but
        # must still match on title/authors text.
        q = q.outerjoin(Abstract.author).filter(
            db.or_(
                Abstract.title.ilike(f"%{search}%"),
                Abstract.authors.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
                User.full_name.ilike(f"%{search}%"),
            )
        )
    if conf_id:
        q = q.filter(Abstract.conference_id == conf_id)

    items = q.options(
        db.joinedload(Abstract.registration),
        db.joinedload(Abstract.author),
    ).order_by(Abstract.created_at.desc()).all()

    conferences = (Conference.query
                   .filter(Conference.deleted_at.is_(None))
                   .order_by(Conference.start_date.desc())
                   .all())
    return render_template("admin/abstracts.html", items=items, status=status,
                           search=search, conf_id=conf_id,
                           conferences=conferences)


@admin_bp.route("/abstracts/<int:aid>", methods=["GET", "POST"])
@requires_permission("abs.review")
def abstract_detail(aid):
    a = Abstract.query.get_or_404(aid)
    review_list = list(a.reviews) if a.reviews else []
    conf_reviewers = (ConferenceReviewer.query
                      .filter_by(conference_id=a.conference_id, is_active=True)
                      .options(db.joinedload(ConferenceReviewer.user))
                      .all())
    conf_reviewers.sort(key=lambda cr: (
        ReviewAssignment.query.filter_by(reviewer_id=cr.user_id).count(),
    ))

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "assign-reviewer":
            reviewer_id = int(request.form.get("reviewer_id", 0))
            if reviewer_id == a.user_id:
                flash("Cannot assign an author as their own reviewer.", "error")
                return redirect(url_for("admin.abstract_detail", aid=a.id))
            existing = ReviewAssignment.query.filter_by(
                abstract_id=a.id, reviewer_id=reviewer_id).first()
            if existing:
                flash("This reviewer is already assigned to this abstract.", "error")
            else:
                ra = ReviewAssignment(
                    abstract_id=a.id, reviewer_id=reviewer_id,
                    status="pending",
                )
                db.session.add(ra)
                db.session.commit()
                flash("Reviewer assigned.", "success")
            return redirect(url_for("admin.abstract_detail", aid=a.id))

        a.status = (request.form.get("status") or a.status).strip()
        a.reviewer_notes = (request.form.get("reviewer_notes") or "").strip()
        a.decided_by_id = current_user.id
        a.decided_at = datetime.utcnow()
        db.session.commit()
        audit.record("abstract.decided",
                     target_kind="abstract", target_id=a.id,
                     summary=f"{a.title}: {a.status}")
        flash("Decision recorded.", "success")
        return redirect(url_for("admin.abstracts"))
    return render_template("admin/abstract_detail.html", a=a,
                           review_list=review_list,
                           conf_reviewers=conf_reviewers)


# ---------------------------------------------------------------------------
# Abstract create/edit (`abs.edit`) — admin-entered abstracts have no
# author account; the authors text field carries attribution.
# ---------------------------------------------------------------------------

def _apply_abstract_form(a: Abstract) -> list[str]:
    """Copy the posted edit form onto `a`. Returns validation errors."""
    errors: list[str] = []
    a.title = (request.form.get("title") or "").strip()
    a.authors = (request.form.get("authors") or "").strip()
    a.body = (request.form.get("body") or "").strip()
    a.track = (request.form.get("track") or "").strip()
    a.presentation_type = (request.form.get("presentation_type") or "Either").strip()
    a.keywords = (request.form.get("keywords") or "").strip()
    a.coi = (request.form.get("coi") or "").strip()
    if not (a.title and a.authors and a.body):
        errors.append("Title, authors, and body are required.")

    try:
        a.website_url = Abstract.clean_website(request.form.get("website_url"))
    except ValueError as e:
        errors.append(str(e))

    status = (request.form.get("status") or "").strip()
    if status:
        if status in ALL_STATUSES:
            a.status = status
        else:
            errors.append(f"Unknown status '{status}'.")

    try:
        a.presenting_author_index = int(
            request.form.get("presenting_author_index", "0") or "0")
    except ValueError:
        a.presenting_author_index = 0

    # References — one DOI (or doi.org URL) per line
    refs: list[dict] = []
    seen: set[str] = set()
    for line in (request.form.get("references") or "").splitlines():
        doi = normalize_doi(line)
        if doi and doi not in seen:
            refs.append({"key": len(refs) + 1, "doi": doi})
            seen.add(doi)
    a.references = refs or None

    # Field validation failed: bail before touching any files, so a
    # rollback can't leave the DB pointing at a removed upload.
    if errors:
        return errors

    # Uploads — save new files first; old files are removed only at the
    # end, once every upload has succeeded, so a failed submit can't
    # leave the DB pointing at a deleted file.
    removals: list[str] = []

    f = request.files.get("figure")
    if f and f.filename:
        try:
            new_fig = save_figure(
                f, upload_folder=current_app.config["UPLOAD_FOLDER"],
                max_bytes=current_app.config["MAX_FIGURE_BYTES"])
        except UploadError as e:
            errors.append(str(e))
        else:
            if a.figure_filename:
                removals.append(a.figure_filename)
            a.figure_filename = new_fig
    elif request.form.get("remove_figure") and a.figure_filename:
        removals.append(a.figure_filename)
        a.figure_filename = None

    pic = request.files.get("profile_picture")
    if pic and pic.filename:
        try:
            rel = save_image(
                pic, upload_folder=current_app.config["UPLOAD_FOLDER"],
                subdir="abstracts", prefix="profile-",
                max_bytes=current_app.config["MAX_HERO_BYTES"],
                target_size=400, force_webp=True)
        except UploadError as e:
            errors.append(str(e))
        else:
            if a.profile_picture_filename:
                removals.append(f"abstracts/{a.profile_picture_filename}")
            a.profile_picture_filename = rel.split("/", 1)[-1]
    elif request.form.get("remove_profile_picture") and a.profile_picture_filename:
        removals.append(f"abstracts/{a.profile_picture_filename}")
        a.profile_picture_filename = None

    if not errors:
        for name in removals:
            remove_upload(current_app.config["UPLOAD_FOLDER"], name)

    return errors


@admin_bp.route("/abstracts/new", methods=["GET", "POST"])
@requires_permission("abs.edit")
def abstract_new():
    conferences = (Conference.query
                   .filter(Conference.deleted_at.is_(None))
                   .order_by(Conference.start_date.desc())
                   .all())
    if request.method == "POST":
        cid = request.form.get("conference_id", type=int)
        c = next((x for x in conferences if x.id == cid), None)
        a = Abstract(user_id=None, conference_id=cid, status="submitted")
        errors = _apply_abstract_form(a)
        if c is None:
            errors.insert(0, "Choose a conference.")

        # Optional author account: attach by email (created if missing).
        # Left blank, the abstract is admin-entered with no account.
        owner_email = (request.form.get("owner_email") or "").strip().lower()
        if owner_email and not errors:
            user = User.query.filter_by(email=owner_email).first()
            if not user:
                user = User(email=owner_email,
                            full_name=a.presenting_author[0] or owner_email,
                            role_name="unregistered")
                db.session.add(user)
                db.session.flush()
            a.user_id = user.id

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("admin/abstract_edit.html", a=a,
                                   conferences=conferences,
                                   statuses=ALL_STATUSES, is_new=True)
        db.session.add(a)
        db.session.commit()

        # Auto-link to the account's registration, as the member flow does.
        if a.user_id and a.registration_id is None:
            reg = Registration.query.filter_by(
                user_id=a.user_id, conference_id=c.id,
                deleted_at=None).first()
            if reg:
                a.registration_id = reg.id
                db.session.commit()

        audit.record("abstract.created",
                     target_kind="abstract", target_id=a.id,
                     summary=f"Admin-entered for {c.slug} "
                             f"({owner_email or 'no account'}): {a.title}")
        flash("Abstract created.", "success")
        return redirect(url_for("admin.abstract_detail", aid=a.id))
    return render_template("admin/abstract_edit.html", a=None,
                           conferences=conferences,
                           statuses=ALL_STATUSES, is_new=True)


@admin_bp.route("/abstracts/<int:aid>/edit", methods=["GET", "POST"])
@requires_permission("abs.edit")
def abstract_edit(aid):
    a = Abstract.query.get_or_404(aid)
    if a.deleted_at is not None:
        flash("This abstract has been deleted.", "error")
        return redirect(url_for("admin.abstracts"))
    if request.method == "POST":
        errors = _apply_abstract_form(a)
        if errors:
            db.session.rollback()
            for err in errors:
                flash(err, "error")
            return render_template("admin/abstract_edit.html", a=a,
                                   conferences=None,
                                   statuses=ALL_STATUSES, is_new=False)
        db.session.commit()
        audit.record("abstract.edited",
                     target_kind="abstract", target_id=a.id,
                     summary=f"{current_user.email} edited \"{a.title}\"")
        flash("Abstract updated.", "success")
        return redirect(url_for("admin.abstract_detail", aid=a.id))
    return render_template("admin/abstract_edit.html", a=a,
                           conferences=None,
                           statuses=ALL_STATUSES, is_new=False)


# ---------------------------------------------------------------------------
# Abstract soft-delete (OTP-confirmed, admin)
# ---------------------------------------------------------------------------

@admin_bp.route("/abstracts/<int:aid>/delete-request", methods=["POST"])
@requires_permission("abs.review", "abs.delete")
def abstract_delete_request(aid):
    a = Abstract.query.get_or_404(aid)
    if a.deleted_at is not None:
        flash("This abstract has already been deleted.", "error")
        return redirect(url_for("admin.abstracts"))
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
        return redirect(url_for("admin.abstracts"))
    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="abstract_delete",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    flash("A confirmation code has been sent to your email.", "success")
    return redirect(url_for("admin.abstract_delete_confirm", aid=a.id))


@admin_bp.route("/abstracts/<int:aid>/delete-confirm", methods=["GET", "POST"])
@requires_permission("abs.review", "abs.delete")
def abstract_delete_confirm(aid):
    a = Abstract.query.get_or_404(aid)
    if a.deleted_at is not None:
        flash("This abstract has already been deleted.", "error")
        return redirect(url_for("admin.abstracts"))
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
            return render_template("admin/abstract_delete_confirm.html", a=a)
        otp.consumed_at = datetime.utcnow()
        title = a.title
        a.deleted_at = datetime.utcnow()
        db.session.commit()
        audit.record("abstract.deleted",
                     target_kind="abstract", target_id=a.id,
                     summary=f"Deleted \"{title}\"")
        flash(f"Deleted abstract \"{title}\".", "success")
        return redirect(url_for("admin.abstracts"))
    return render_template("admin/abstract_delete_confirm.html", a=a)


# ---------------------------------------------------------------------------
# Review system — reviewer management, allocation, overview
# ---------------------------------------------------------------------------

@admin_bp.route("/conferences/<int:cid>/reviewers", methods=["GET", "POST"])
@requires_permission("abs.review", "conf.edit")
def conference_reviewers(cid):
    c = Conference.query.get_or_404(cid)
    tracks = c.tracks_list()
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        expertise = ", ".join(request.form.getlist("expertise"))
        max_reviews = int(request.form.get("max_reviews", 5))
        user = User.query.filter_by(email=email, deleted_at=None).first()
        if not user:
            flash("No user found with that email address.", "error")
            return redirect(url_for("admin.conference_reviewers", cid=c.id))
        existing = ConferenceReviewer.query.filter_by(
            conference_id=c.id, user_id=user.id).first()
        if existing:
            flash(f"{user.full_name or user.email} is already a reviewer.", "error")
            return redirect(url_for("admin.conference_reviewers", cid=c.id))
        cr = ConferenceReviewer(
            conference_id=c.id, user_id=user.id,
            expertise=expertise, max_reviews=max_reviews,
        )
        db.session.add(cr)
        db.session.commit()
        flash(f"Added {user.full_name or user.email} as reviewer.", "success")
        return redirect(url_for("admin.conference_reviewers", cid=c.id))

    crs = (ConferenceReviewer.query
           .filter_by(conference_id=c.id)
           .order_by(ConferenceReviewer.created_at.desc())
           .all())
    users = {cr.user_id: cr.user for cr in crs}
    return render_template("admin/conference_reviewers.html", c=c,
                           reviewers=crs, users=users, tracks=tracks)


@admin_bp.route("/conferences/<int:cid>/reviewers/<int:rid>/edit", methods=["POST"])
@requires_permission("abs.review", "conf.edit")
def conference_reviewer_edit(cid, rid):
    cr = ConferenceReviewer.query.filter_by(id=rid, conference_id=cid).first_or_404()
    cr.expertise = ", ".join(request.form.getlist("expertise"))
    cr.max_reviews = int(request.form.get("max_reviews", 5))
    cr.is_active = bool(request.form.get("is_active"))
    db.session.commit()
    flash("Reviewer updated.", "success")
    return redirect(url_for("admin.conference_reviewers", cid=cid))


@admin_bp.route("/conferences/<int:cid>/reviewers/<int:rid>/remove", methods=["POST"])
@requires_permission("abs.review", "conf.edit")
def conference_reviewer_remove(cid, rid):
    cr = ConferenceReviewer.query.filter_by(id=rid, conference_id=cid).first_or_404()
    conf_abstract_ids = [a.id for a in Abstract.query.filter_by(conference_id=cid).all()]
    if conf_abstract_ids:
        ReviewAssignment.query.filter(
            ReviewAssignment.reviewer_id == cr.user_id,
            ReviewAssignment.abstract_id.in_(conf_abstract_ids),
            ReviewAssignment.status == "pending",
        ).delete(synchronize_session=False)
    db.session.delete(cr)
    db.session.commit()
    flash("Reviewer removed. Any pending assignments were deleted.", "success")
    return redirect(url_for("admin.conference_reviewers", cid=cid))


@admin_bp.route("/conferences/<int:cid>/allocate-reviews", methods=["POST"])
@requires_permission("abs.review", "conf.edit")
def conference_allocate_reviews(cid):
    c = Conference.query.get_or_404(cid)
    n_per_paper = c.reviewers_per_paper or 2

    active_reviewers = (ConferenceReviewer.query
                        .filter_by(conference_id=c.id, is_active=True)
                        .all())
    if not active_reviewers:
        flash("No active reviewers assigned to this conference.", "error")
        return redirect(url_for("admin.conference_reviewers", cid=c.id))

    abstracts = (Abstract.query
                 .filter_by(conference_id=c.id, deleted_at=None)
                 .filter(Abstract.status == "submitted")
                 .all())
    if not abstracts:
        flash("No submitted abstracts to allocate.", "info")
        return redirect(url_for("admin.conference_reviewers", cid=c.id))

    allocated = 0
    failed_no_candidates = 0
    failed_at_capacity = 0
    failed_author_only = 0

    for a in abstracts:
        existing_count = (ReviewAssignment.query
                          .filter_by(abstract_id=a.id).count())
        if existing_count >= n_per_paper:
            continue

        needed = n_per_paper - existing_count

        candidates = []
        skipped_self = False
        skipped_declined = 0
        skipped_capacity = 0
        for cr in active_reviewers:
            if cr.user_id == a.user_id:
                skipped_self = True
                continue
            declined_this = ReviewAssignment.query.filter_by(
                abstract_id=a.id, reviewer_id=cr.user_id,
                status="declined").first()
            if declined_this:
                skipped_declined += 1
                continue
            assigned_count = (ReviewAssignment.query
                               .filter_by(reviewer_id=cr.user_id)
                               .filter(ReviewAssignment.status != "declined")
                               .count())
            if assigned_count >= cr.max_reviews:
                skipped_capacity += 1
                continue

            score = _expertise_match(cr.expertise, a.track)
            current_count = (ReviewAssignment.query
                             .filter_by(reviewer_id=cr.user_id,
                                        status="pending").count())
            candidates.append((cr, score, current_count))

        candidates.sort(key=lambda x: (-x[1], x[2]))

        for cr, _, _ in candidates[:needed]:
            existing = ReviewAssignment.query.filter_by(
                abstract_id=a.id, reviewer_id=cr.user_id).first()
            if existing:
                continue
            ra = ReviewAssignment(
                abstract_id=a.id, reviewer_id=cr.user_id,
                status="pending",
            )
            db.session.add(ra)
            allocated += 1

        assigned_after = (ReviewAssignment.query
                          .filter_by(abstract_id=a.id).count())
        still_needed = n_per_paper - assigned_after
        if still_needed > 0:
            if not candidates:
                if skipped_self and skipped_capacity >= len(active_reviewers) - 1:
                    if len(active_reviewers) == 1:
                        failed_author_only += 1
                    else:
                        failed_at_capacity += 1
                else:
                    failed_no_candidates += 1
            else:
                failed_at_capacity += 1

    db.session.commit()

    if allocated == 0 and abstracts:
        flash(
            f"Could not allocate any reviews. "
            f"{failed_author_only} abstracts have only themselves as reviewer, "
            f"{failed_no_candidates} have no eligible reviewers, "
            f"{failed_at_capacity} have all reviewers at capacity.",
            "error",
        )
    elif allocated > 0:
        parts = [f"Allocated {allocated} review assignments across {len(abstracts)} abstracts."]
        if failed_no_candidates:
            parts.append(f"{failed_no_candidates} abstracts could not be allocated — no eligible reviewers (check expertise settings or recusals).")
        if failed_at_capacity:
            parts.append(f"{failed_at_capacity} abstracts could not be allocated — all reviewers at maximum capacity.")
        if failed_author_only:
            parts.append(f"{failed_author_only} abstracts could not be allocated — the only possible reviewer is the author.")
        flash(" ".join(parts),
              "warning" if (failed_no_candidates or failed_at_capacity or failed_author_only) else "success")
    return redirect(url_for("admin.conference_reviewers", cid=c.id))


def _expertise_match(expertise: str, track: str) -> int:
    """Score how well reviewer expertise matches a track (0-100)."""
    if not expertise or not track:
        return 50
    exp_tokens = set(expertise.lower().replace(",", " ").split())
    track_tokens = set(track.lower().split())
    if not exp_tokens or not track_tokens:
        return 50
    overlap = exp_tokens & track_tokens
    if not overlap:
        return 30
    raw = min(100, int(100 * len(overlap) / max(len(exp_tokens), len(track_tokens))))
    return max(30, raw)


@admin_bp.route("/conferences/<int:cid>/reviews")
@requires_permission("abs.review")
def conference_reviews(cid):
    c = Conference.query.get_or_404(cid)
    n_per_paper = c.reviewers_per_paper or 2
    abstracts = (Abstract.query
                 .filter_by(conference_id=c.id, deleted_at=None)
                 .filter(Abstract.status == "submitted")
                 .order_by(Abstract.created_at.asc())
                 .all())

    active_reviewers = (ConferenceReviewer.query
                        .filter_by(conference_id=c.id, is_active=True)
                        .all())
    reviewer_users = {cr.user_id: cr.user.email for cr in active_reviewers}
    reviewer_users.update({cr.user_id: cr.user.full_name for cr in active_reviewers})

    under_assigned = []
    for a in abstracts:
        ra_count = sum(1 for r in a.reviews if r.status != "declined")
        if ra_count < n_per_paper:
            overbooked = []
            for cr in active_reviewers:
                if cr.user_id == a.user_id:
                    continue
                declined = any(r.status == "declined" and r.reviewer_id == cr.user_id
                              for r in a.reviews)
                if declined:
                    continue
                assigned = sum(1 for r in a.reviews if r.reviewer_id == cr.user_id)
                if assigned > 0:
                    continue
                over_count = ReviewAssignment.query.filter_by(
                    reviewer_id=cr.user_id).count()
                if over_count >= cr.max_reviews:
                    overbooked.append((cr, over_count))
            if overbooked:
                under_assigned.append((a, ra_count, overbooked))

    threshold_preview = request.args.get("threshold", type=int)
    target_count = request.args.get("target", type=int)

    scored = []
    for a in abstracts:
        completed = [r for r in a.reviews if r.status == "completed"]
        if len(completed) >= n_per_paper and a.mean_score is not None:
            scored.append((a, a.mean_score))
    scored.sort(key=lambda x: -x[1])

    threshold_info = None
    if threshold_preview is not None:
        accepted = sum(1 for _, ms in scored if ms >= threshold_preview)
        threshold_info = {
            "value": threshold_preview,
            "accepted": accepted,
            "total_scored": len(scored),
        }
    elif target_count is not None and scored:
        target = max(1, min(target_count, len(scored)))
        threshold_for_target = scored[target - 1][1] if target <= len(scored) else scored[-1][1]
        threshold_info = {
            "value": threshold_for_target,
            "accepted": target,
            "total_scored": len(scored),
            "from_target": True,
        }

    return render_template("admin/conference_reviews.html", c=c,
                           abstracts=abstracts,
                           under_assigned=under_assigned,
                           n_per_paper=n_per_paper,
                           threshold_info=threshold_info,
                           scored_count=len(scored),
                           already_accepted=sum(
                               1 for a in abstracts if a.status == "accepted"))


@admin_bp.route("/conferences/<int:cid>/accept-overflow", methods=["POST"])
@requires_permission("abs.review", "conf.edit")
def conference_accept_overflow(cid):
    c = Conference.query.get_or_404(cid)
    abstract_id = int(request.form.get("abstract_id", 0))
    reviewer_id = int(request.form.get("reviewer_id", 0))

    a = Abstract.query.get_or_404(abstract_id)
    if a.conference_id != c.id:
        abort(404)

    cr = ConferenceReviewer.query.filter_by(
        conference_id=c.id, user_id=reviewer_id).first()
    if not cr:
        flash("Reviewer not found for this conference.", "error")
        return redirect(url_for("admin.conference_reviews", cid=c.id))

    existing = ReviewAssignment.query.filter_by(
        abstract_id=a.id, reviewer_id=reviewer_id).first()
    if existing:
        flash("Assignment already exists.", "error")
        return redirect(url_for("admin.conference_reviews", cid=c.id))

    ra = ReviewAssignment(
        abstract_id=a.id, reviewer_id=reviewer_id, status="pending",
    )
    db.session.add(ra)
    db.session.commit()
    flash(f"Assigned overflow review to {cr.user.email if cr.user else reviewer_id}.", "success")
    return redirect(url_for("admin.conference_reviews", cid=c.id))


@admin_bp.route("/conferences/<int:cid>/bulk-decision", methods=["POST"])
@requires_permission("abs.review")
def conference_bulk_decision(cid):
    c = Conference.query.get_or_404(cid)
    threshold = int(request.form.get("threshold", 70))
    action = (request.form.get("action") or "").strip()

    if action not in ("accept", "reject", "revise"):
        flash("Invalid action.", "error")
        return redirect(url_for("admin.conference_reviews", cid=c.id))

    status_map = {"accept": "accepted", "reject": "rejected", "revise": "revise"}

    changed = 0
    abstracts = (Abstract.query
                 .filter_by(conference_id=c.id, deleted_at=None)
                 .filter(Abstract.status == "submitted")
                 .options(db.joinedload(Abstract.reviews))
                 .all())
    for a in abstracts:
        ms = a.mean_score
        if ms is None:
            continue
        completed = [r for r in a.reviews if r.status == "completed"]
        if len(completed) < (c.reviewers_per_paper or 2):
            continue
        if action == "accept" and ms >= threshold:
            a.status = "accepted"
            changed += 1
        elif action == "reject" and ms < threshold:
            a.status = "rejected"
            changed += 1
        elif action == "revise" and ms < threshold:
            a.status = "revise"
            changed += 1

    db.session.commit()
    verb = {"accept": "Accepted", "reject": "Rejected", "revise": "Sent for revision"}
    flash(f"{verb.get(action, 'Updated')} {changed} abstracts.", "success")
    return redirect(url_for("admin.conference_reviews", cid=c.id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug_taken(slug: str, exclude_id: int | None = None) -> bool:
    q = Conference.query.filter_by(slug=slug)
    if exclude_id is not None:
        q = q.filter(Conference.id != exclude_id)
    return db.session.query(q.exists()).scalar()


def _unfeature_others(exclude_id: int | None = None) -> None:
    q = Conference.query.filter(
        Conference.deleted_at.is_(None),
        Conference.is_featured.is_(True),
    )
    if exclude_id is not None:
        q = q.filter(Conference.id != exclude_id)
    q.update({Conference.is_featured: False}, synchronize_session=False)


# ---------------------------------------------------------------------------
# Compile abstract booklet (LaTeX source zip)
# ---------------------------------------------------------------------------

_KNOWN_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}


def _convert_for_latex(src: Path, dst: Path) -> Path:
    """Ensure *src* is a LaTeX-compatible image, writing to *dst* as needed.

    pdfLaTeX supports PNG, JPG, and PDF natively.  WEBP and TIFF are *not*
    supported, so we transcode them to PNG.  If the source is already
    compatible we just copy the bytes.  Returns the destination path
    (may differ from *dst* if the suffix was changed).
    """
    from PIL import Image

    ext = src.suffix.lower()
    if ext in {".png", ".jpg", ".jpeg"}:
        dst.write_bytes(src.read_bytes())
        return dst
    if ext == ".pdf":
        dst.write_bytes(src.read_bytes())
        return dst
    try:
        img = Image.open(src)
        img = img.convert("RGB")
        png_dst = dst.with_suffix(".png")
        img.save(png_dst, "PNG", optimize=True)
        return png_dst
    except Exception:
        dst.write_bytes(src.read_bytes())
        return dst


@admin_bp.route("/conferences/<int:cid>/compile-booklet", methods=["POST"])
@requires_permission("abs.compile_booklet")
def conference_compile_booklet(cid):
    import hashlib
    import tempfile
    import zipfile

    c = Conference.query.get_or_404(cid)
    abstracts = (
        Abstract.query
        .filter_by(conference_id=c.id)
        .filter(Abstract.status.in_(SPEAKER_STATUSES))
        .filter(Abstract.deleted_at.is_(None))
        .order_by(Abstract.created_at.asc())
        .all()
    )
    if not abstracts:
        flash("No accepted abstracts to compile.", "error")
        return redirect(url_for("admin.abstracts"))

    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])
    cache_dir = uploads_root / "abstracts" / ".booklet-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_key = hashlib.sha256(
        ",".join(
            [f"{a.id}:{a.status}:{a.updated_at.isoformat()}" for a in abstracts]
            + [
                c.booklet_header_filename or "",
                c.booklet_footer_filename or "",
                c.booklet_background_filename or "",
            ]
        ).encode()
    ).hexdigest()

    cache_file = cache_dir / f"{c.slug}-{cache_key[:12]}.zip"
    pdf_cache_file = cache_dir / f"{c.slug}-{cache_key[:12]}.pdf"

    for old in cache_dir.glob(f"{c.slug}-*.zip"):
        if old != cache_file:
            old.unlink(missing_ok=True)

    action = request.form.get("booklet_action", "latex")

    if action == "pdf" and pdf_cache_file.exists():
        return send_file(pdf_cache_file, as_attachment=True,
                         download_name=f"booklet-{c.slug}.pdf")

    if action != "pdf" and cache_file.exists():
        return send_file(cache_file, as_attachment=True,
                         download_name=f"abstracts-{c.slug}.zip")

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp)

        def _copy_booklet_image(col_name: str, label: str) -> str | None:
            filename = getattr(c, col_name, None)
            if not filename:
                return None
            img_src = uploads_root / "conferences" / filename
            if not img_src.exists():
                return None
            dst = src / f"{label}{img_src.suffix}"
            result = _convert_for_latex(img_src, dst)
            return result.name

        header_rel = _copy_booklet_image("booklet_header_filename", "header")
        footer_rel = _copy_booklet_image("booklet_footer_filename", "footer")
        bg_rel = _copy_booklet_image("booklet_background_filename", "background")

        inputs: list[str] = []
        for i, a in enumerate(abstracts, 1):
            label = f"{i:03d}"
            sub = src / f"abstract_{label}"
            sub.mkdir(parents=True, exist_ok=True)

            frag = _abstract_fragment(label, a, has_header=header_rel is not None,
                                      has_background=bg_rel is not None)
            (sub / f"abstract_{label}.tex").write_text(frag, encoding="utf-8")
            inputs.append(f"\\input{{abstract_{label}/abstract_{label}.tex}}")

            if a.figure_filename:
                bare = a.figure_filename.split("/", 1)[-1]
                fig_src = uploads_root / "abstracts" / bare
                if fig_src.exists():
                    _convert_for_latex(fig_src, sub / f"figure{fig_src.suffix}")

            if a.profile_picture_filename:
                bare = a.profile_picture_filename.split("/", 1)[-1]
                pic_src = uploads_root / "abstracts" / bare
                if pic_src.exists():
                    _convert_for_latex(pic_src, sub / f"profile{pic_src.suffix}")

        preamble = _booklet_preamble(c, inputs, header_rel, footer_rel, bg_rel)
        (src / "booklet.tex").write_text(preamble, encoding="utf-8")

        if action == "pdf":
            return _compile_pdf(src, c, pdf_cache_file)
        else:
            zip_path = cache_file
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in sorted(src.rglob("*")):
                    if f.is_file():
                        zf.write(f, str(f.relative_to(src)))
            return send_file(zip_path, as_attachment=True,
                             download_name=f"abstracts-{c.slug}.zip")


def _compile_pdf(src: Path, c: Conference, cache_file: Path):
    import subprocess
    tex_file = src / "booklet.tex"
    try:
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-output-directory",
             str(src), str(tex_file)],
            check=True, capture_output=True, timeout=120,
        )
        pdf = src / "booklet.pdf"
        if pdf.exists():
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(pdf, cache_file)
            return send_file(cache_file, as_attachment=True,
                             download_name=f"booklet-{c.slug}.pdf")
        flash("PDF compilation produced no output. Check the LaTeX source for errors.", "error")
    except subprocess.CalledProcessError as e:
        flash(f"PDF compilation failed. See error details below.", "error")
        return ("<pre>" + e.stderr.decode("utf-8", errors="replace")[:5000] + "</pre>", 200,
                {"Content-Type": "text/html"})
    except FileNotFoundError:
        flash("pdflatex is not installed on this server. Install texlive to enable PDF compilation.", "error")
    except subprocess.TimeoutExpired:
        flash("PDF compilation timed out (2 minute limit). The booklet may be too large.", "error")
    return redirect(url_for("admin.conference_edit", cid=c.id))


# ---------------------------------------------------------------------------
# LaTeX template helpers
# ---------------------------------------------------------------------------

# The LaTeX text-mode escape lives with the document renderer now (single home
# for the escaping table); the booklet export reuses it via this import.
from ...services.documents import latex_escape as _latex_escape


def _booklet_preamble(conference, inputs: list[str],
                      header_rel: str | None,
                      footer_rel: str | None,
                      bg_rel: str | None) -> str:
    title_esc = conference.title.replace("&", "\\&").replace("#", "\\#")
    date_esc = conference.date_range

    pkgs = [
        "\\documentclass[11pt,a4paper]{article}",
        "\\usepackage[margin=25.4mm,headheight=14pt,footskip=18pt]{geometry}",
        "\\usepackage{helvet}",
        "\\renewcommand{\\familydefault}{\\sfdefault}",
        "\\usepackage{setspace}",
        "\\setstretch{1.15}",
        "\\usepackage{graphicx}",
        "\\usepackage{hyperref}",
        "\\usepackage{parskip}",
        "\\usepackage{fancyhdr}",
    ]
    if bg_rel:
        pkgs.append("\\usepackage[pages=all]{background}")

    pkgs.append("")
    pkgs.append("\\pagestyle{fancy}")
    pkgs.append("\\fancyhf{}")
    pkgs.append("\\renewcommand{\\headrulewidth}{0.4pt}")

    if header_rel:
        pkgs.append(
            "\\fancyhead[L]{\\includegraphics[height=1.3cm,keepaspectratio]"
            f"{{{header_rel}}}}}"
        )
    else:
        pkgs.append(f"\\fancyhead[L]{{\\small\\itshape {title_esc}}}")
    pkgs.append("\\fancyhead[R]{\\small\\thepage}")

    if footer_rel:
        pkgs.append(
            "\\fancyfoot[R]{\\includegraphics[height=0.9cm,keepaspectratio]"
            f"{{{footer_rel}}}}}"
        )
    else:
        pkgs.append("\\fancyfoot[C]{}")

    if bg_rel:
        pkgs.append("\\backgroundsetup{")
        pkgs.append(f"  contents={{\\includegraphics[width=\\paperwidth,height=\\paperheight]{{{bg_rel}}}}},")
        pkgs.append("  opacity=0.06,")
        pkgs.append("  scale=1,")
        pkgs.append("}")

    pkgs.append("")
    pkgs.append(f"\\title{{{title_esc}}}")
    pkgs.append("\\author{Abstract Booklet}")
    pkgs.append(f"\\date{{{date_esc}}}")
    pkgs.append("")
    pkgs.append("\\begin{document}")
    pkgs.append("\\thispagestyle{empty}")
    if bg_rel:
        pkgs.append("\\NoBgThispage")
    pkgs.append("\\maketitle")
    pkgs.append("\\tableofcontents")
    pkgs.append("\\newpage")
    pkgs.append("")
    pkgs.extend(inputs)
    pkgs.append("")
    pkgs.append("\\end{document}")

    return "\n".join(pkgs)


def _abstract_fragment(label: str, abstract,
                       has_header: bool = False,
                       has_background: bool = False) -> str:
    """Return LaTeX fragment matching the abstract preview page layout.

    Centred title (bold), centred authors with superscript affiliations
    and presenting author underlined, centred italic affiliations,
    career-stage / presentation-preference meta, justified body,
    figure filling remaining space, and numbered DOI references.
    """
    folder = f"abstract_{label}"
    title = abstract.title.replace("&", "\\&").replace("#", "\\#").replace("_", "\\_")

    body = abstract.body
    _BSL = "\x00BSL\x00"
    body = body.replace("\\", _BSL)
    body = body.replace("&", "\\&").replace("#", "\\#")
    body = body.replace("$", "\\$").replace("%", "\\%")
    body = body.replace("{", "\\{").replace("}", "\\}")
    body = body.replace("~", "\\textasciitilde{}").replace("^", "\\^{}")
    body = body.replace(_BSL, "\\textbackslash{}")
    body = body.replace("\r\n", "\n")
    body = body.replace("\n\n", "\n\n\\medskip\n\n")

    def _out_ext(filename: str | None) -> str:
        if not filename:
            return ""
        ext = Path(filename).suffix.lower()
        if ext in _KNOWN_IMAGE_EXTS:
            return ".png" if ext in {".webp", ".tif", ".tiff"} else ext
        return ext

    presenting_idx = abstract.presenting_author_index or 0
    author_line, affil_line = _parse_authors(abstract.authors, presenting_idx)

    lines: list[str] = []

    # Build TOC text: title + first author et al.
    toc_text = title
    first_author = ""
    if abstract.authors:
        first_line = abstract.authors.strip().split("\n")[0]
        first_name = first_line.split("|")[0].strip()
        if first_name:
            first_author = first_name.replace("&", "\\&").replace("#", "\\#").replace("_", "\\_")
    if first_author:
        total = len([ln for ln in abstract.authors.strip().split("\n") if ln.strip()])
        if total > 1:
            toc_text = f"{title} --- {first_author} \\textit{{et al.}}"
        else:
            toc_text = f"{title} --- {first_author}"
    lines.append(f"\\addcontentsline{{toc}}{{section}}{{{toc_text}}}")

    if has_background:
        lines.append("\\BgThispage")

    lines.append("\\begin{center}")
    lines.append(f"  {{\\LARGE\\bfseries {title}\\par}}")
    lines.append("\\end{center}")

    if author_line:
        lines.append("\\begin{center}")
        lines.append(f"  {{\\large {author_line}\\par}}")
        lines.append("\\end{center}")

    if affil_line:
        lines.append("\\begin{center}")
        lines.append(f"  {{\\large\\itshape {affil_line}\\par}}")
        lines.append("\\end{center}")

    cd = abstract.custom_data or {}
    career = (cd.get("career-stage") or "").strip()
    pres = (cd.get("presentation-preference") or "").strip()
    meta_bits: list[str] = []
    if career:
        meta_bits.append(career)
    if pres:
        meta_bits.append(pres)
    if meta_bits:
        lines.append("\\begin{center}")
        lines.append(f"  {{\\small\\textit{{{'  \\textperiodcentered{}  '.join(meta_bits)}}}\\par}}")
        lines.append("\\end{center}")

    lines.append("")
    lines.append(body)

    # References (compact, before figure, small font)
    refs = abstract.references or []
    if refs:
        lines.append("")
        lines.append("\\textbf{\\small References}")
        lines.append("\\begin{enumerate}")
        lines.append("\\small")
        for ref in refs:
            meta = fetch_metadata(ref["doi"])
            if meta:
                cite = _latex_escape(format_reference_compact(meta))
            else:
                cite = ref["doi"].replace("_", "\\_")
            doi_esc = ref["doi"].replace("_", "\\_")
            lines.append(f"  \\item \\href{{https://doi.org/{doi_esc}}}{{{cite}}}")
        lines.append("\\end{enumerate}")

    if abstract.figure_filename:
        out = _out_ext(abstract.figure_filename)
        lines.append("")
        lines.append("\\vspace*{\\fill}")
        lines.append("\\begin{center}")
        lines.append(
            "\\includegraphics[\n"
            "    width=\\textwidth,\n"
            "    height=\\dimexpr\\textheight-\\pagetotal-4ex\\relax,\n"
            "    keepaspectratio\n"
            f"  ]{{{folder}/figure{out}}}"
        )
        lines.append("\\end{center}")

    lines.append("")
    lines.append("\\newpage")
    return "\n".join(lines)


def _parse_authors(raw: str, presenting_idx: int = 0) -> tuple[str, str]:
    """Parse pipe-delimited author rows into LaTeX-formatted lines.

    Returns ``(author_line, affil_line)``.  Author names carry
    ``\\textsuperscript{…}`` affiliation markers.  The presenting author
    (by index) is wrapped in ``\\underline{…}``.
    """
    if not raw or not raw.strip():
        return ("", "")

    authors: list[tuple[str, str, str]] = []
    affil_map: dict[str, str] = {}
    seen_affils: set[str] = set()

    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        name = parts[0].strip() if len(parts) > 0 else ""
        idx = parts[1].strip() if len(parts) > 1 else ""
        affil = parts[2].strip() if len(parts) > 2 else ""
        if name:
            authors.append((name, idx, affil))
            if idx and affil and affil not in seen_affils:
                seen_affils.add(affil)
                affil_map[idx] = affil

    if not authors:
        return ("", "")

    author_names: list[str] = []
    for i, (name, idx, _affil) in enumerate(authors):
        name_esc = name.replace("&", "\\&").replace("#", "\\#").replace("_", "\\_")
        if idx:
            tag = f"{name_esc}\\textsuperscript{{{idx}}}"
        else:
            tag = name_esc
        if i == presenting_idx:
            tag = f"\\underline{{{tag}}}"
        author_names.append(tag)
    author_line = ", ".join(author_names)

    affil_parts: list[str] = []
    for idx in sorted(affil_map.keys(), key=int):
        affil_esc = affil_map[idx].replace("&", "\\&").replace("#", "\\#").replace("_", "\\_")
        affil_parts.append(f"\\textsuperscript{{{idx}}}{affil_esc}")
    affil_line = "\\quad ".join(affil_parts)

    return (author_line, affil_line)
