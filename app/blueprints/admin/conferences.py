"""Admin → Conferences: CRUD with audit, soft delete via OTP, abstract
review, and per-conference price tiers.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from . import admin_bp
from ...extensions import db
from ...models import Abstract, Conference, OTPCode, Registration, Sponsor, SponsorTier
from ...models.conference import PriceTier
from ...security import requires_permission, audit
from ...services.mail import send_mail
from ...services.slugs import slugify
from ...services.uploads import (
    UploadError, remove_upload, save_image, save_pdf,
)


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

        # -- Price tiers --
        for t in list(c.price_tiers):
            if request.form.get(f"tier_delete_{t.id}"):
                db.session.delete(t)
                continue
            t.name = (request.form.get(f"tier_name_{t.id}") or t.name).strip()
            try:
                t.amount = int(request.form.get(f"tier_amount_{t.id}") or t.amount)
            except ValueError:
                pass
            t.description = (request.form.get(f"tier_desc_{t.id}") or "").strip()
            try:
                t.display_order = int(request.form.get(f"tier_order_{t.id}") or 0)
            except ValueError:
                pass

        # Add new price tiers
        new_names = request.form.getlist("new_tier_name[]")
        new_amounts = request.form.getlist("new_tier_amount[]")
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
            db.session.add(PriceTier(
                conference_id=c.id,
                name=name,
                amount=amount,
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
            st.name = (request.form.get(f"stier_name_{st.id}") or st.name).strip()
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
                s.name = (request.form.get(f"sponsor_name_{s.id}") or s.name).strip()
                s.url = (request.form.get(f"sponsor_url_{s.id}") or "").strip() or None
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
    db.session.add(OTPCode(
        email=current_user.email.lower(),
        code=code,
        purpose="conference_delete",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    ))
    db.session.commit()
    send_mail(
        to=current_user.email,
        subject="Confirm conference deletion",
        body=(f"You requested to delete the conference “{c.title}”.\n\n"
              f"Confirmation code: {code}\n\n"
              f"This code expires in {ttl // 60} minutes. "
              f"If you didn't request this, ignore the email."),
    )
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
