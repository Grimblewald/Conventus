"""First-run setup wizard.

* When `instance/.setup-complete` is absent, the factory's `before_request`
  gatekeeper redirects every URL here.
* On first GET to `/setup/welcome`, we generate `instance/setup-pw` (random
  32-char token, mode 0600) and print it to the gunicorn console. Operators
  retrieve it from server stdout / logs.
* The user enters that token, then steps through:
    1.  Admin account (email + name)
    2.  Site identity (name, tagline, contact email, currency)
    3.  Palette + fonts (pre-filled from placeholder.yaml)
    4.  Review + finish
* On success: write `.setup-complete`, delete `setup-pw`, log the user in.
* The wizard becomes inaccessible afterwards (the gatekeeper passes through
  and `/setup/*` returns 404 because the blueprint refuses to handle it).
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template,
    request, session, url_for,
)
from flask_login import login_user

from ...extensions import db
from ...models.user import ensure_roles_exist
from ...security import audit
from ...services.fonts import FONT_STACKS, all_choices
from ...services.seeding import load_placeholder_yaml, seed_initial


log = logging.getLogger(__name__)
setup_bp = Blueprint("setup", __name__, template_folder="../../templates/setup")


# ---------------------------------------------------------------------------
# Gatekeeper helpers
# ---------------------------------------------------------------------------

def _setup_complete() -> bool:
    return Path(current_app.config["SETUP_FLAG_PATH"]).exists()


def _ensure_setup_password() -> str:
    """Create the setup-pw file if it doesn't exist. Return its contents."""
    p = Path(current_app.config["SETUP_PASSWORD_PATH"])
    if p.exists():
        return p.read_text(encoding="utf-8").strip()

    p.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    p.write_text(token + "\n", encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass

    bar = "=" * 72
    print(f"\n{bar}", flush=True)
    print(" FIRST-RUN SETUP REQUIRED", flush=True)
    print(f"{bar}", flush=True)
    print(" Open the site and visit /setup. Use this one-time password:", flush=True)
    print(f"     {token}", flush=True)
    print(f" Stored at {p}", flush=True)
    print(" It will be DELETED automatically after setup completes.", flush=True)
    print(f"{bar}\n", flush=True)
    return token


def _password_ok(supplied: str) -> bool:
    p = Path(current_app.config["SETUP_PASSWORD_PATH"])
    if not p.exists():
        return False
    expected = p.read_text(encoding="utf-8").strip()
    return secrets.compare_digest(supplied.strip(), expected)


def _finalise(user) -> None:
    """Mark setup complete, delete setup-pw."""
    flag = Path(current_app.config["SETUP_FLAG_PATH"])
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(datetime.utcnow().isoformat() + "\n", encoding="utf-8")
    pw = Path(current_app.config["SETUP_PASSWORD_PATH"])
    if pw.exists():
        try:
            pw.unlink()
        except OSError:
            log.warning("Could not delete setup-pw file at %s", pw)
    audit.record("setup.completed",
                 target_kind="setup",
                 summary=f"First-run setup completed by {user.email}")


# ---------------------------------------------------------------------------
# Refuse to handle anything after setup is done.
# ---------------------------------------------------------------------------

@setup_bp.before_request
def _refuse_after_setup():
    if _setup_complete():
        abort(404)


# ---------------------------------------------------------------------------
# Step 0 — welcome / password
# ---------------------------------------------------------------------------

@setup_bp.route("/", methods=["GET"])
def root():
    return redirect(url_for("setup.welcome"))


@setup_bp.route("/welcome", methods=["GET", "POST"])
def welcome():
    _ensure_setup_password()  # generate-and-print on first GET
    db.create_all()           # bootstrap schema if it doesn't exist yet
    ensure_roles_exist()      # so admin role exists for assignment later

    if request.method == "POST":
        if _password_ok(request.form.get("password", "")):
            session["setup_unlocked"] = True
            return redirect(url_for("setup.wizard"))
        flash("That password didn't match. Check the server console output.",
              "error")
    return render_template("setup/welcome.html")


# ---------------------------------------------------------------------------
# Step 1+ — the wizard form
# ---------------------------------------------------------------------------

def _require_unlocked():
    if not session.get("setup_unlocked"):
        return redirect(url_for("setup.welcome"))
    return None


@setup_bp.route("/wizard", methods=["GET", "POST"])
def wizard():
    redir = _require_unlocked()
    if redir:
        return redir

    yaml_data = load_placeholder_yaml(
        Path(current_app.config["PROJECT_ROOT"]) / "placeholder.yaml"
    )

    if request.method == "POST":
        # Build the seeding dict by overlaying form values on top of YAML.
        merged = _merge_form_over_yaml(request.form, yaml_data)
        try:
            user = seed_initial(merged)
        except Exception as e:
            log.exception("Setup seeding failed")
            flash(f"Could not complete setup: {e}", "error")
            return render_template(
                "setup/wizard.html",
                data=merged, font_choices=all_choices(), FONT_STACKS=FONT_STACKS,
            )

        _finalise(user)
        session.pop("setup_unlocked", None)
        login_user(user, remember=False)
        flash(
            "Setup complete — welcome! The wizard has been disabled. "
            "Continue customising your site from the admin panel.",
            "success",
        )
        return redirect(url_for("admin.index"))

    return render_template(
        "setup/wizard.html",
        data=yaml_data,
        font_choices=all_choices(),
        FONT_STACKS=FONT_STACKS,
    )


# ---------------------------------------------------------------------------
# Form-merge helper
# ---------------------------------------------------------------------------

def _merge_form_over_yaml(form, yaml_data: dict) -> dict:
    """Take the wizard form and produce a seed-shaped dict.

    Anything missing from the form falls back to placeholder.yaml.
    """
    yaml_data = dict(yaml_data or {})

    site = dict(yaml_data.get("site") or {})
    for k in ("name", "tagline", "short_name", "browser_tab_title",
              "copyright_line", "contact_email", "locale", "timezone",
              "currency_code", "currency_symbol"):
        v = (form.get(f"site_{k}") or "").strip()
        if v:
            site[k] = v
    yaml_data["site"] = site

    admin = dict(yaml_data.get("admin") or {})
    admin["email"] = (form.get("admin_email") or admin.get("email") or "").strip().lower()
    admin["full_name"] = (form.get("admin_full_name") or admin.get("full_name") or "").strip()
    yaml_data["admin"] = admin

    palette = dict(yaml_data.get("palette") or {})
    for k in (
        "page_bg", "page_text", "muted_text", "link", "link_hover",
        "accent", "accent_ink", "header_bg", "header_text",
        "footer_bg", "footer_text", "card_bg", "card_border",
        "button_bg", "button_text",
    ):
        v = (form.get(f"palette_{k}") or "").strip()
        if v:
            palette[k] = v
    yaml_data["palette"] = palette

    fonts = dict(yaml_data.get("fonts") or {})
    for k in ("heading", "body", "link", "ui"):
        v = (form.get(f"font_{k}") or "").strip()
        if v:
            fonts[k] = v
    yaml_data["fonts"] = fonts

    return yaml_data
