"""Admin → Sponsors: per-conference tiers and logos, managed inline on the
conference edit page."""
from __future__ import annotations

from flask import current_app, flash, redirect, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import Conference
from ...models.sponsor import Sponsor, SponsorTier
from ...security import requires_permission, audit
from ...services.uploads import UploadError, remove_upload, save_image


@admin_bp.route("/conferences/<int:cid>/sponsors", methods=["POST"])
@requires_permission("sponsors.edit", "conf.edit")
def conference_sponsors(cid: int):
    c = Conference.query.get_or_404(cid)
    action = request.form.get("action")

    if action == "add_tier":
        name = (request.form.get("name") or "Tier").strip()
        order = len(c.sponsor_tiers) * 10 + 10
        db.session.add(SponsorTier(conference_id=c.id, name=name, display_order=order))
        db.session.commit()
        audit.record("sponsor_tier.added", target_kind="sponsor_tier",
                     summary=f"+ {name} for {c.slug}")
        flash(f"Sponsor tier “{name}” added.", "success")

    elif action == "delete_tier":
        try:
            t = SponsorTier.query.get(int(request.form.get("tier_id", "")))
        except (TypeError, ValueError):
            t = None
        if t and t.conference_id == c.id:
            for s in t.sponsors:
                _remove_sponsor_logo(s)
            db.session.delete(t)
            db.session.commit()
            audit.record("sponsor_tier.deleted", target_kind="sponsor_tier",
                         summary=f"Deleted {t.name}")
            flash(f"Tier “{t.name}” removed.", "success")

    elif action == "save_tiers":
        for t in c.sponsor_tiers:
            t.name = (request.form.get(f"tier_name_{t.id}") or t.name).strip()
            try:
                t.display_order = int(request.form.get(f"tier_order_{t.id}") or t.display_order)
            except ValueError:
                pass
        db.session.commit()
        flash("Sponsor tiers saved.", "success")

    elif action == "add_sponsor":
        try:
            tier = SponsorTier.query.get(int(request.form.get("tier_id", "")))
        except (TypeError, ValueError):
            tier = None
        if not tier or tier.conference_id != c.id:
            flash("Invalid tier.", "error")
            return redirect(url_for("admin.conference_edit", cid=c.id))

        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Sponsor name is required.", "error")
            return redirect(url_for("admin.conference_edit", cid=c.id))

        s = Sponsor(
            tier_id=tier.id,
            name=name,
            url=(request.form.get("url") or "").strip() or None,
            display_order=len(tier.sponsors) * 10 + 10,
        )

        logo = request.files.get("logo")
        if logo and logo.filename:
            try:
                rel = save_image(
                    logo, upload_folder=current_app.config["UPLOAD_FOLDER"],
                    subdir="sponsors", prefix=f"sponsor-{c.id}",
                    max_bytes=current_app.config["MAX_HERO_BYTES"],
                    target_size=400,
                )
                s.logo_filename = rel.split("/", 1)[-1]
            except UploadError as e:
                flash(str(e), "error")
                return redirect(url_for("admin.conference_edit", cid=c.id))

        db.session.add(s)
        db.session.commit()
        audit.record("sponsor.added", target_kind="sponsor",
                     summary=f"{s.name} → {tier.name} ({c.slug})")
        flash(f"Sponsor “{s.name}” added to {tier.name}.", "success")

    elif action == "delete_sponsor":
        try:
            s = Sponsor.query.get(int(request.form.get("sponsor_id", "")))
        except (TypeError, ValueError):
            s = None
        if s and s.tier and s.tier.conference_id == c.id:
            _remove_sponsor_logo(s)
            db.session.delete(s)
            db.session.commit()
            audit.record("sponsor.deleted", target_kind="sponsor",
                         summary=f"Deleted {s.name}")
            flash(f"Sponsor “{s.name}” removed.", "success")

    return redirect(url_for("admin.conference_edit", cid=c.id))


def _remove_sponsor_logo(s: Sponsor) -> None:
    if s.logo_filename:
        remove_upload(current_app.config["UPLOAD_FOLDER"],
                      f"sponsors/{s.logo_filename}")
