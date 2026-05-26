"""Admin blueprint package.

The admin surface is large enough that we keep each concern in its own
module under `app/blueprints/admin/` and stitch them together here.
"""
from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import current_user

from ...models import (
    Abstract, Announcement, Conference, Registration, User,
)
from ...security import staff_required, can


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
    )


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
