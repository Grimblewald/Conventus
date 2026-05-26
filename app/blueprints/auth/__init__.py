"""Auth blueprint: hardened email-OTP sign-in.

Hardening over the original:
* Rate limited per IP and per email (Flask-Limiter).
* Prior unconsumed codes for an email are invalidated when a new one is
  issued.
* Attempt counter on each OTP — too many wrong tries marks the *code*
  consumed.
* Per-user lockout window after repeated failures.
* `purpose=login` so an admin-action OTP can't be used to sign in.
* Audit log row for every successful login.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from email_validator import EmailNotValidError, validate_email
from flask import (
    Blueprint, current_app, flash, redirect, render_template,
    request, session, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from ...extensions import db, limiter
from ...models import OTPCode, User
from ...security import audit
from ...services.mail import send_mail


auth_bp = Blueprint("auth", __name__, template_folder="../../templates/auth")


# ---------------------------------------------------------------------------
# OTP issuance helper
# ---------------------------------------------------------------------------

def issue_login_otp(email: str) -> None:
    email = email.lower().strip()

    # Invalidate prior unconsumed login codes for this email.
    (OTPCode.query
        .filter_by(email=email, purpose="login", consumed_at=None)
        .update({"consumed_at": datetime.utcnow()}))

    code = f"{secrets.randbelow(1_000_000):06d}"
    ttl = current_app.config["OTP_TTL_SECONDS"]
    otp = OTPCode(
        email=email,
        code=code,
        purpose="login",
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ip=request.remote_addr,
    )
    db.session.add(otp)
    db.session.commit()

    site_name = current_app.config.get("SITE_NAME_FALLBACK", "Your Society")
    send_mail(
        to=email,
        subject=f"Your {site_name} sign-in code",
        body=(
            f"Your one-time sign-in code is: {code}\n\n"
            f"It expires in {ttl // 60} minutes. "
            f"If you didn't request it, you can safely ignore this email."
        ),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("8 per hour;3 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("public.home"))

    if request.method == "POST":
        raw = (request.form.get("email") or "").strip()
        try:
            email = validate_email(raw, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            flash("Please enter a valid email address.", "error")
            return render_template("auth/login.html", email=raw)

        # Check existing user lockout (if any) — silently delay if so.
        user = User.query.filter_by(email=email).first()
        if user and user.locked_until and user.locked_until > datetime.utcnow():
            flash(
                "Too many failed attempts for that address. "
                "Please try again later.", "error",
            )
            return render_template("auth/login.html", email=email)

        issue_login_otp(email)
        session["pending_email"] = email
        return redirect(url_for("auth.verify"))

    return render_template("auth/login.html", email="")


@auth_bp.route("/verify", methods=["GET", "POST"])
@limiter.limit("20 per hour;6 per minute", methods=["POST"])
def verify():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        entered = (request.form.get("code") or "").strip().replace(" ", "")
        max_attempts = current_app.config["OTP_MAX_ATTEMPTS"]
        lockout_seconds = current_app.config["OTP_LOCKOUT_SECONDS"]

        otp = (
            OTPCode.query
            .filter_by(email=email, purpose="login", consumed_at=None)
            .order_by(OTPCode.id.desc())
            .first()
        )

        if not (otp and otp.is_valid()):
            flash("That code has expired. Request a new one below.", "error")
            return render_template("auth/verify.html", email=email)

        otp.attempt_count = (otp.attempt_count or 0) + 1
        if otp.code != entered:
            # Burn the code if too many attempts and apply a lockout window.
            if otp.attempt_count >= max_attempts:
                otp.consumed_at = datetime.utcnow()
                u = User.query.filter_by(email=email).first()
                if u:
                    u.locked_until = (datetime.utcnow()
                                      + timedelta(seconds=lockout_seconds))
                db.session.commit()
                flash(
                    "Too many incorrect attempts. Please try again later.",
                    "error",
                )
                return render_template("auth/verify.html", email=email)
            db.session.commit()
            flash(
                f"That code didn't match. "
                f"{max_attempts - otp.attempt_count} attempt(s) left.",
                "error",
            )
            return render_template("auth/verify.html", email=email)

        # Success.
        otp.consumed_at = datetime.utcnow()
        user = User.query.filter_by(email=email).first()
        is_new = user is None
        if is_new:
            user = User(email=email, role_name="unregistered")
            db.session.add(user)
        user.locked_until = None
        user.last_login_at = datetime.utcnow()
        db.session.commit()

        login_user(user, remember=True)
        session.pop("pending_email", None)
        audit.record("user.login",
                     target_kind="user", target_id=user.id,
                     summary=f"{user.email} signed in")

        if is_new:
            flash("Welcome! Complete your profile to finish signing up.", "success")
            return redirect(url_for("member.profile"))
        flash("Signed in.", "success")
        return redirect(url_for("member.dashboard"))

    return render_template("auth/verify.html", email=email)


@auth_bp.route("/resend", methods=["POST"])
@limiter.limit("5 per hour;2 per minute")
def resend():
    email = session.get("pending_email")
    if email:
        issue_login_otp(email)
        flash("A new code is on its way.", "success")
    return redirect(url_for("auth.verify"))


@auth_bp.route("/logout")
@login_required
def logout():
    audit.record("user.logout",
                 target_kind="user", target_id=current_user.id,
                 summary=f"{current_user.email} signed out")
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("public.home"))
