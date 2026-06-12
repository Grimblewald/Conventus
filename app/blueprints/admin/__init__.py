"""Admin blueprint package.

The admin surface is large enough that we keep each concern in its own
module under `app/blueprints/admin/` and stitch them together here.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ...extensions import db
from ...models import (
    Abstract, Announcement, Conference, Registration, User,
)
from ...security import staff_required, can
from ...services.updater import latest_status


admin_bp = Blueprint("admin", __name__, template_folder="../../templates/admin")


# Expose `can()` as a Jinja global so admin templates can hide buttons that
# the current user is not permitted to use.
@admin_bp.app_context_processor
def _inject_can():
    return {"can": can}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@admin_bp.route("/")
@staff_required
def index():
    stats = {
        "members": User.query.filter(User.deleted_at.is_(None)).count(),
        "conferences": Conference.query.filter(Conference.deleted_at.is_(None)).count(),
        "abstracts": Abstract.query.filter(Abstract.deleted_at.is_(None)).count(),
        "registrations": Registration.query.filter(Registration.deleted_at.is_(None)).count(),
        "pending_abstracts": (
            Abstract.query
            .filter(Abstract.deleted_at.is_(None), Abstract.status == "submitted")
            .count()
        ),
    }
    recent_abs = (
        Abstract.query
        .filter(Abstract.deleted_at.is_(None))
        .order_by(Abstract.created_at.desc())
        .limit(8).all()
    )
    recent_regs = (
        Registration.query
        .filter(Registration.deleted_at.is_(None))
        .order_by(Registration.created_at.desc())
        .limit(8).all()
    )
    recent_anns = (
        Announcement.query
        .filter(Announcement.deleted_at.is_(None))
        .order_by(Announcement.published_at.desc())
        .limit(5).all()
    )
    return render_template(
        "admin/index.html",
        stats=stats,
        recent_abs=recent_abs,
        recent_regs=recent_regs,
        recent_anns=recent_anns,
        update=latest_status(),
    )


# ---------------------------------------------------------------------------
# Registrations list — view and manually mark as paid.
# ---------------------------------------------------------------------------

@admin_bp.route("/registrations")
@staff_required
def registrations():
    conference_id = request.args.get("conference_id", type=int)
    status_filter = request.args.get("status", "all")
    query = Registration.query.filter(Registration.deleted_at.is_(None))
    if conference_id:
        query = query.filter_by(conference_id=conference_id)
    if status_filter != "all":
        query = query.filter_by(status=status_filter)
    regs = query.order_by(Registration.created_at.desc()).all()
    conf_list = (
        Conference.query
        .filter(Conference.deleted_at.is_(None))
        .order_by(Conference.start_date.desc())
        .all()
    )
    return render_template(
        "admin/registrations.html",
        regs=regs,
        conferences=conf_list,
        conference_id=conference_id,
        status_filter=status_filter,
    )


@admin_bp.route("/registrations/<int:reg_id>/status", methods=["POST"])
@staff_required
def registration_status(reg_id):
    reg = Registration.query.get_or_404(reg_id)
    new_status = (request.form.get("status") or "").strip()
    if new_status in ("pending", "paid", "refunded", "cancelled"):
        reg.status = new_status
        db.session.commit()
        from ...security import audit as audit_log
        audit_log.record(
            "registration.status_changed",
            target_kind="registration", target_id=reg.id,
            summary=f"{current_user.email} → {reg.status}",
        )
        flash(f"Registration marked as {new_status}.", "success")
    return redirect(url_for("admin.registrations"))


# ---------------------------------------------------------------------------
# Sub-modules: each registers more routes on the same blueprint.
# Imported for their side effects.
# ---------------------------------------------------------------------------
from . import site          # noqa: F401, E402
from . import pages         # noqa: F401, E402
from . import nav           # noqa: F401, E402
from . import footer        # noqa: F401, E402
from . import committee     # noqa: F401, E402
from . import conferences   # noqa: F401, E402
from . import announcements # noqa: F401, E402
from . import users         # noqa: F401, E402
from . import permissions   # noqa: F401, E402
from . import audit         # noqa: F401, E402
from . import api           # noqa: F401, E402
from . import sponsors      # noqa: F401, E402
from . import past_boards   # noqa: F401, E402
from . import backup        # noqa: F401, E402
