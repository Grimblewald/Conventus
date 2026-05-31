"""Public, unauthenticated routes: home, conferences, committee, contact,
custom pages, served uploads.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    send_from_directory, url_for,
)
from flask_login import current_user

from ...extensions import db, limiter
from ...models import (
    Announcement, CommitteeMember, Conference, Page, User,
)
from ...services.mail import send_mail


public_bp = Blueprint("public", __name__)


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@public_bp.route("/")
def home():
    today = date.today()
    featured = (
        Conference.query
        .filter(Conference.deleted_at.is_(None), Conference.is_featured.is_(True))
        .order_by(Conference.start_date)
        .first()
    )
    upcoming = (
        Conference.query
        .filter(
            Conference.deleted_at.is_(None),
            Conference.is_draft.is_(False),
            Conference.end_date >= today,
        )
        .order_by(Conference.start_date)
        .all()
    )
    announcements = (
        Announcement.query
        .filter(Announcement.deleted_at.is_(None))
        .order_by(Announcement.pinned.desc(), Announcement.published_at.desc())
        .limit(6)
        .all()
    )
    if featured and featured.auto_reopen():
        db.session.commit()
    return render_template(
        "public/home.html",
        featured=featured,
        upcoming=upcoming,
        announcements=announcements,
    )


# ---------------------------------------------------------------------------
# Conferences
# ---------------------------------------------------------------------------

@public_bp.route("/conferences")
def conferences():
    items = (
        Conference.query
        .filter(Conference.deleted_at.is_(None), Conference.is_draft.is_(False))
        .order_by(Conference.start_date.desc())
        .all()
    )
    return render_template("public/conferences.html", conferences=items)


@public_bp.route("/conferences/<slug>")
def conference_detail(slug):
    c = (Conference.query
         .filter_by(slug=slug)
         .filter(Conference.deleted_at.is_(None))
         .first_or_404())
    if c.is_draft and not (current_user.is_authenticated
                            and current_user.has_permission("conf.view_drafts")):
        abort(404)
    if c.auto_reopen():
        db.session.commit()
    return render_template("public/conference_detail.html", c=c)


# ---------------------------------------------------------------------------
# Committee
# ---------------------------------------------------------------------------

@public_bp.route("/committee")
def committee():
    items = CommitteeMember.visible_in_order()
    return render_template("public/committee.html", items=items)


# ---------------------------------------------------------------------------
# Custom Markdown pages (About, Privacy, Terms, Code of Conduct, custom)
# ---------------------------------------------------------------------------

@public_bp.route("/p/<slug>")
def page(slug):
    p = (Page.query
         .filter_by(slug=slug, published=True)
         .filter(Page.deleted_at.is_(None))
         .first_or_404())
    return render_template("public/page.html", page=p)


# ---------------------------------------------------------------------------
# Contact form
# ---------------------------------------------------------------------------

@public_bp.route("/contact", methods=["GET", "POST"])
@limiter.limit("5 per hour;2 per minute", methods=["POST"])
def contact():
    recipients = (
        User.query
        .filter(User.role_name.in_(("admin", "committee")),
                User.deleted_at.is_(None),
                User.full_name.isnot(None),
                User.full_name != "")
        .order_by(User.full_name)
        .all()
    )
    if request.method == "POST":
        # Honeypot — if a bot fills `website`, silently no-op success.
        if request.form.get("confirm_human", "").strip():
            flash("Message sent.", "success")
            return redirect(url_for("public.contact"))

        try:
            rid = int(request.form.get("recipient_id", ""))
        except ValueError:
            rid = 0
        target = next((u for u in recipients if u.id == rid), None)
        sender_name = (request.form.get("name", "") or "").strip()
        sender_email = (request.form.get("email", "") or "").strip()
        subject = (request.form.get("subject", "") or "").strip() or "Contact form enquiry"
        message = (request.form.get("message", "") or "").strip()

        if not (target and sender_name and sender_email and message):
            flash("Please fill in every field.", "error")
            return render_template("public/contact.html",
                                   recipients=recipients, form=request.form)

        body = (f"From: {sender_name} <{sender_email}>\n"
                f"Sent via the contact form.\n\n{message}\n")
        send_mail(target.email, f"[Contact] {subject}", body)
        flash(f"Message sent to {target.full_name}.", "success")
        return redirect(url_for("public.contact"))

    return render_template("public/contact.html", recipients=recipients, form={})


# ---------------------------------------------------------------------------
# Served uploads — keep behind sensible checks.
# ---------------------------------------------------------------------------

@public_bp.route("/uploads/site/<path:name>")
def site_upload(name):
    """Site-wide images (logo, favicon, hero, OG) — always public."""
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "site"
    return send_from_directory(folder, name)


@public_bp.route("/uploads/committee/<path:name>")
def committee_upload(name):
    """Committee portraits — public."""
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "committee"
    return send_from_directory(folder, name)


@public_bp.route("/uploads/conferences/<path:name>")
def conference_upload(name):
    """Conference assets (hero, booklet) — public."""
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "conferences"
    return send_from_directory(folder, name)


@public_bp.route("/uploads/sponsors/<path:name>")
def sponsor_upload(name):
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / "sponsors"
    return send_from_directory(folder, name)


@public_bp.route("/favicon.ico")
def favicon():
    from ...models import get_site_settings
    s = get_site_settings()
    if s.favicon_filename:
        return redirect(url_for("public.site_upload", name=s.favicon_filename))
    abort(404)
