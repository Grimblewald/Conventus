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
from ...models import Abstract, Conference, OTPCode, OrganisingCommitteeMember, Registration, Sponsor, SponsorTier, SubEvent, User
from ...models.abstract import SPEAKER_STATUSES
from ...models.conference import PriceTier
from ...security import requires_permission, audit
from ...services.mail import send_mail
from ...services.slugs import slugify
from ...services.uploads import (
    UploadError, save_image, save_pdf, remove_upload,
)
from ...services.citations import fetch_metadata, format_reference_compact


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
        c.is_accepting_abstracts = not bool(request.form.get("not_accepting_abstracts"))
        c.is_accepting_registrations = not bool(request.form.get("not_accepting_registrations"))
        c.external_registration_url = (request.form.get("external_registration_url") or "").strip() or None
        c.external_abstract_url = (request.form.get("external_abstract_url") or "").strip() or None
        raw_max = (request.form.get("max_abstracts_per_user") or "").strip()
        c.max_abstracts_per_user = int(raw_max) if raw_max else None

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
                    t.amount = int(request.form.get(f"tier_amount_{t.id}") or 0)
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
                        t.early_bird_amount = int(request.form.get(f"tier_eb_amt_{t.id}") or 0)
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
                amount = int(new_amounts[i] or 0)
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
                eb_amt = int(raw_eb) if raw_eb else None
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
        for i, name in enumerate(new_st_names):
            name = name.strip()
            if not name:
                continue
            order = 0
            try:
                order = int(new_st_orders[i] or 0)
            except (IndexError, ValueError):
                pass
            db.session.add(SponsorTier(
                conference_id=c.id,
                name=name,
                display_order=order,
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
# Manual abstract submission (on behalf of a speaker)
# ---------------------------------------------------------------------------

@admin_bp.route("/conferences/<int:cid>/add-abstract", methods=["POST"])
@requires_permission("conf.edit")
def conference_add_abstract(cid):
    c = Conference.query.get_or_404(cid)
    try:
        owner_email = (request.form.get("owner_email") or "").strip().lower()
        if not owner_email:
            raise ValueError("Abstract owner email is required.")
        full_name = (request.form.get("full_name") or "").strip()
        title = (request.form.get("abs_title") or "").strip()
        authors = (request.form.get("abs_authors") or "").strip()
        body = (request.form.get("abs_body") or "").strip()
        track = (request.form.get("abs_track") or "").strip()
        status = (request.form.get("abs_status") or "accepted").strip()
        if not title:
            raise ValueError("Title is required.")
        if not authors:
            if full_name:
                authors = f"{full_name}||"
            else:
                raise ValueError("Authors field is required.")

        user = User.query.filter_by(email=owner_email).first()
        if not user:
            user = User(email=owner_email, full_name=full_name or owner_email,
                        role_name="unregistered")
            db.session.add(user)
            db.session.flush()

        a = Abstract(
            user_id=user.id,
            conference_id=c.id,
            title=title,
            authors=authors,
            body=body or "",
            track=track,
            status=status,
            decided_by_id=current_user.id,
        )

        pic = request.files.get("abs_profile_picture")
        if pic and pic.filename:
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

        db.session.add(a)
        db.session.commit()
        audit.record("abstract.admin_created",
                     target_kind="abstract", target_id=a.id,
                     summary=f"Added on behalf of {owner_email} for {c.slug}: {title}")
        flash(f"Abstract \"{title}\" added for {owner_email}.", "success")
    except UploadError as e:
        flash(str(e), "error")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not add abstract: {e}", "error")
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
    q = Abstract.query.filter(Abstract.deleted_at.is_(None))
    if status != "all":
        q = q.filter_by(status=status)
    items = q.order_by(Abstract.created_at.desc()).all()
    return render_template("admin/abstracts.html", items=items, status=status)


@admin_bp.route("/abstracts/<int:aid>", methods=["GET", "POST"])
@requires_permission("abs.review")
def abstract_detail(aid):
    a = Abstract.query.get_or_404(aid)
    if request.method == "POST":
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
    return render_template("admin/abstract_detail.html", a=a)


# ---------------------------------------------------------------------------
# Abstract soft-delete (OTP-confirmed, admin)
# ---------------------------------------------------------------------------

@admin_bp.route("/abstracts/<int:aid>/delete-request", methods=["POST"])
@requires_permission("abs.review")
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
@requires_permission("abs.review")
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

    cache_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "abstracts" / ".booklet-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{c.slug}-{cache_key[:12]}.zip"

    for old in cache_dir.glob(f"{c.slug}-*.zip"):
        if old != cache_file:
            old.unlink(missing_ok=True)

    if cache_file.exists():
        return send_file(cache_file, as_attachment=True,
                         download_name=f"abstracts-{c.slug}.zip")

    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp)

        # --- Booklet decoration images (header / footer / background) ---
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

        # --- Abstract subfolders ---
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

        zip_path = cache_file
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    zf.write(f, str(f.relative_to(src)))

    return send_file(zip_path, as_attachment=True,
                     download_name=f"abstracts-{c.slug}.zip")


# ---------------------------------------------------------------------------
# LaTeX template helpers
# ---------------------------------------------------------------------------

def _latex_escape(text: str) -> str:
    """Escape special characters for LaTeX text mode."""
    text = text.replace("\\", "\\textbackslash{}")
    text = text.replace("&", "\\&")
    text = text.replace("%", "\\%")
    text = text.replace("$", "\\$")
    text = text.replace("#", "\\#")
    text = text.replace("_", "\\_")
    text = text.replace("{", "\\{")
    text = text.replace("}", "\\}")
    text = text.replace("~", "\\textasciitilde{}")
    text = text.replace("^", "\\^{}")
    return text


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
