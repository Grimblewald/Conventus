"""Admin → Site → Palette / Fonts / Images / Identity.

Splits the old "floating gear" into four semantically-grouped tabs, each
gated by its own permission.
"""
from __future__ import annotations

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for

from . import admin_bp
from ...extensions import db
from ...models import get_site_settings
from ...security import requires_permission, audit
from ...services.fonts import FONT_STACKS, all_choices
from ...services.uploads import UploadError, remove_upload, save_image


# ---------------------------------------------------------------------------
# Shared landing
# ---------------------------------------------------------------------------

@admin_bp.route("/site")
@requires_permission(
    "site.palette", "site.fonts", "site.images", "site.identity",
)
def site_index():
    return redirect(url_for("admin.site_identity"))


# ---------------------------------------------------------------------------
# Identity (site name, tagline, browser tab, contact email, locale, currency)
# ---------------------------------------------------------------------------

@admin_bp.route("/site/identity", methods=["GET", "POST"])
@requires_permission("site.identity")
def site_identity():
    s = get_site_settings()
    if request.method == "POST":
        for fld in (
            "site_name", "tagline", "short_name", "browser_tab_title",
            "copyright_line", "contact_email", "locale", "timezone",
            "currency_code", "currency_symbol",
            "intro_paragraph", "closing_statement",
        ):
            val = (request.form.get(fld) or "").strip()
            if val:
                setattr(s, fld, val)
        s.payment_portal_enabled = request.form.get("payment_portal_enabled") == "1"
        db.session.commit()
        audit.record("site.identity_updated",
                     target_kind="site_settings", target_id=s.id,
                     summary="Site identity edited")
        flash("Identity updated.", "success")
        return redirect(url_for("admin.site_identity"))
    return render_template("admin/site_identity.html", s=s)


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

PALETTE_FIELDS = (
    "palette_page_bg", "palette_page_text", "palette_muted_text",
    "palette_link", "palette_link_hover",
    "palette_accent", "palette_accent_ink",
    "palette_header_bg", "palette_header_text",
    "palette_footer_bg", "palette_footer_text",
    "palette_card_bg", "palette_card_border",
    "palette_button_bg", "palette_button_text",
)


def _valid_color(v: str) -> bool:
    v = v.strip()
    if not v:
        return False
    # Loose CSS-colour check: #rgb, #rrggbb, #rrggbbaa, or oklch(...).
    import re
    if re.match(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$", v):
        return True
    if re.match(r"^(rgb|rgba|hsl|hsla|oklch|oklab|color)\([^)]+\)$", v):
        return True
    return False


@admin_bp.route("/site/palette", methods=["GET", "POST"])
@requires_permission("site.palette")
def site_palette():
    s = get_site_settings()
    if request.method == "POST":
        changed: dict[str, str] = {}
        for fld in PALETTE_FIELDS:
            v = (request.form.get(fld) or "").strip()
            if v and _valid_color(v) and getattr(s, fld) != v:
                setattr(s, fld, v)
                changed[fld] = v
        if changed:
            db.session.commit()
            audit.record("site.palette_updated",
                         target_kind="site_settings", target_id=s.id,
                         summary=f"Palette: {len(changed)} field(s) changed",
                         metadata=changed)
            flash("Palette saved.", "success")
        else:
            flash("Nothing changed.", "error")
        return redirect(url_for("admin.site_palette"))
    return render_template("admin/site_palette.html",
                           s=s, fields=PALETTE_FIELDS)


# JSON endpoint used by the live preview in the editor — write a single
# field at a time and return the updated palette for the page CSS to pick up.
@admin_bp.route("/site/palette/quick", methods=["POST"])
@requires_permission("site.palette")
def site_palette_quick():
    s = get_site_settings()
    payload = request.get_json(silent=True) or {}
    changed = {}
    for k, v in payload.items():
        if k in PALETTE_FIELDS and _valid_color(str(v)):
            setattr(s, k, str(v))
            changed[k] = str(v)
    if changed:
        db.session.commit()
        audit.record("site.palette_updated",
                     target_kind="site_settings", target_id=s.id,
                     summary="Palette quick-edit", metadata=changed)
    return jsonify({"ok": True, "palette": s.palette})


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

@admin_bp.route("/site/fonts", methods=["GET", "POST"])
@requires_permission("site.fonts")
def site_fonts():
    s = get_site_settings()
    if request.method == "POST":
        keys = ("font_heading", "font_body", "font_link", "font_ui")
        changed = {}
        for k in keys:
            v = (request.form.get(k) or "").strip()
            if v in FONT_STACKS and getattr(s, k) != v:
                setattr(s, k, v)
                changed[k] = v
        if changed:
            db.session.commit()
            audit.record("site.fonts_updated",
                         target_kind="site_settings", target_id=s.id,
                         summary=f"Fonts: {len(changed)} field(s) changed",
                         metadata=changed)
            flash("Fonts saved.", "success")
        else:
            flash("Nothing changed.", "error")
        return redirect(url_for("admin.site_fonts"))
    return render_template("admin/site_fonts.html",
                           s=s, font_choices=all_choices(), FONT_STACKS=FONT_STACKS)


# ---------------------------------------------------------------------------
# Images (logo, favicon, hero, OG)
# ---------------------------------------------------------------------------

IMAGE_SLOTS = {
    "logo":    {"label": "Logo",               "size": 800,  "square": False, "ext_field": "logo_filename"},
    "favicon": {"label": "Browser-tab favicon", "size": 256, "square": True,  "ext_field": "favicon_filename"},
    "hero":    {"label": "Home hero image",     "size": 1920, "square": False, "ext_field": "hero_image_filename"},
    "og":      {"label": "Social share image",  "size": 1200, "square": False, "ext_field": "og_image_filename"},
}


@admin_bp.route("/site/images", methods=["GET"])
@requires_permission("site.images")
def site_images():
    s = get_site_settings()
    return render_template("admin/site_images.html", s=s, slots=IMAGE_SLOTS)


@admin_bp.route("/site/images/<slot>", methods=["POST"])
@requires_permission("site.images")
def site_images_update(slot):
    if slot not in IMAGE_SLOTS:
        flash("Unknown image slot.", "error")
        return redirect(url_for("admin.site_images"))
    cfg = IMAGE_SLOTS[slot]
    s = get_site_settings()
    if request.form.get("remove"):
        remove_upload(current_app.config["UPLOAD_FOLDER"],
                      f"site/{getattr(s, cfg['ext_field'])}"
                      if getattr(s, cfg['ext_field']) else None)
        setattr(s, cfg["ext_field"], None)
        db.session.commit()
        audit.record("site.image_removed",
                     target_kind="site_settings", target_id=s.id,
                     summary=f"Removed {cfg['label']}")
        flash(f"{cfg['label']} removed.", "success")
        return redirect(url_for("admin.site_images"))

    f = request.files.get("image")
    try:
        rel = save_image(
            f,
            upload_folder=current_app.config["UPLOAD_FOLDER"],
            subdir="site",
            prefix=slot,
            max_bytes=current_app.config["MAX_HERO_BYTES"],
            square_crop=cfg["square"],
            target_size=cfg["size"],
        )
    except UploadError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.site_images"))

    # rel = "site/<filename>" — but we only want the bare filename in the DB.
    old = getattr(s, cfg["ext_field"])
    if old:
        remove_upload(current_app.config["UPLOAD_FOLDER"], f"site/{old}")
    setattr(s, cfg["ext_field"], rel.split("/", 1)[-1])
    db.session.commit()
    audit.record("site.image_uploaded",
                 target_kind="site_settings", target_id=s.id,
                 summary=f"Uploaded new {cfg['label']}")
    flash(f"{cfg['label']} updated.", "success")
    return redirect(url_for("admin.site_images"))
