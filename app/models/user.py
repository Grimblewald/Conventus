"""User, role and role-permission models.

We use a *named-role* model with a static catalogue of permissions instead
of arbitrary per-user grants. Roles ship as: `unregistered`, `member`,
`committee`, `admin`. Admin has every permission and cannot be edited.

`BUILT_IN_PERMISSIONS` is the *single source of truth* for what an admin
can toggle on the Permissions panel. Adding a new gated capability =
adding one entry here + checking it with `requires_permission(...)`.
"""
from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin

from ..extensions import db


ROLE_NAMES = ("unregistered", "member", "committee", "admin")
EDITABLE_ROLE_NAMES = ("unregistered", "member", "committee")  # admin is fixed-true


# ---------------------------------------------------------------------------
# Permission catalogue
# ---------------------------------------------------------------------------
#   (key, group, label, description)
# Keep groups short — they become section headings on the permissions panel.

BUILT_IN_PERMISSIONS: tuple[tuple[str, str, str, str], ...] = (
    # Conferences
    ("conf.view_drafts",    "Conferences",   "View draft conferences",
     "See conferences flagged as drafts and unpublished events."),
    ("conf.create",         "Conferences",   "Create conferences",
     "Add new conferences from the admin panel."),
    ("conf.edit",           "Conferences",   "Edit conferences",
     "Modify any conference's content, dates, tiers and uploads."),
    ("conf.delete",         "Conferences",   "Delete conferences",
     "Permanently remove conferences (requires an email OTP)."),

    # Abstracts
    ("abs.review",          "Abstracts",     "Review abstract submissions",
     "Read, decide on, and add reviewer notes to submitted abstracts."),
    ("abs.edit",            "Abstracts",     "Create and edit abstracts",
     "Create abstracts without an author account (for invited and plenary "
     "speakers) and edit any abstract's content, authors, and track."),
    ("abs.export",          "Abstracts",     "Export abstracts",
     "Download abstract bundles as ZIP/CSV."),
    ("abs.compile_booklet", "Abstracts",     "Compile abstract booklet",
     "Generate a LaTeX booklet zip of all accepted abstracts for a conference."),
    ("abs.delete",          "Abstracts",     "Delete abstracts",
     "Soft-delete abstracts (requires OTP confirmation)."),

    # Announcements
    ("ann.publish",         "Announcements", "Publish announcements",
     "Create, edit and pin announcements on the home page."),
    ("ann.delete",          "Announcements", "Delete announcements",
     "Soft-delete announcements (recoverable for 30 days)."),

    # Committee
    ("committee.edit_self", "Committee",     "Edit own committee profile",
     "Edit the committee profile attached to this user, if any."),
    ("committee.edit_any",  "Committee",     "Edit any committee profile",
     "Create / edit / reorder all committee member entries."),

    # Pages & navigation
    ("pages.edit",          "Site content",  "Edit static pages",
     "Edit About, custom pages, privacy, code of conduct, terms."),
    ("pages.delete",        "Site content",  "Delete pages",
     "Soft-delete static pages (recoverable)."),
    ("nav.edit",            "Site content",  "Edit navigation menu",
     "Add, remove, or reorder top-level navigation links."),
    ("footer.edit",         "Site content",  "Edit footer",
     "Edit footer columns, links and the copyright line."),

    # Aesthetic
    ("site.palette",        "Appearance",    "Change site palette",
     "Edit header / footer / link / button colours under Site → Palette."),
    ("site.fonts",          "Appearance",    "Change site fonts",
     "Choose heading / body / link fonts under Site → Fonts."),
    ("site.images",         "Appearance",    "Replace site images",
     "Upload logo, favicon, hero image, OG image."),
    ("site.identity",       "Appearance",    "Edit site identity",
     "Change site name, tagline, browser tab title."),

    # Sponsors
    ("sponsors.edit",       "Conferences",   "Manage sponsors",
     "Add, edit, and reorder sponsor tiers and logos per conference."),

    # Registrations
    ("registrations.view",  "Registrations", "View registrations",
     "List and view details of all conference registrations."),
    ("registrations.edit",  "Registrations", "Manage registrations",
     "Change payment status and delete registrations."),

    # Members
    ("users.view",          "Members",       "View member directory",
     "List all registered users with role and affiliation."),
    ("users.edit",          "Members",       "Edit member profiles & roles",
     "Promote / demote members and committee. Cannot affect admins."),
    ("users.email_bulk",    "Members",       "Send bulk emails",
     "Compose and send an email to a filtered list of members."),
    ("users.export",        "Members",       "Export member directory",
     "Download a CSV of registered members."),

    # System
    ("system.backup",       "System",        "Backup & restore",
     "Create and restore full-site backup archives."),
)

# When key K is granted, every key in IMPLICIT_PERMISSIONS[K] is
# also considered granted.  Enforced at both check-time (so a
# user with registrations.edit can always see the list) and
# save-time (so the Permissions panel keeps the DB consistent).
IMPLICIT_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "registrations.edit": ("registrations.view",),
    "users.edit":         ("users.view",),
    "users.email_bulk":   ("users.view",),
    "abs.delete":         ("abs.review",),
    "ann.delete":         ("ann.publish",),
    "pages.delete":       ("pages.edit",),
    "conf.delete":        ("conf.view_drafts",),
}


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(200))
    affiliation = db.Column(db.String(200))

    # FK to roles.name so changes to the role name cascade naturally.
    role_name = db.Column(
        db.String(32),
        db.ForeignKey("roles.name", onupdate="CASCADE"),
        default="unregistered",
        nullable=False,
        index=True,
    )

    # Authentication / hardening
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Soft delete
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)

    role = db.relationship("Role", lazy="joined")
    registrations = db.relationship("Registration", backref="user", lazy=True)
    # NB: `abstracts` is defined as a backref on Abstract.author with an
    # explicit foreign_keys=[Abstract.user_id], because Abstract has two FKs
    # into users (the author + the deciding reviewer). Without that
    # disambiguation, SQLAlchemy raises AmbiguousForeignKeysError on first
    # query.
    committee_profile = db.relationship(
        "CommitteeMember", backref="user", uselist=False, lazy="joined",
    )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------
    @property
    def is_admin(self) -> bool:
        return self.role_name == "admin"

    @property
    def is_committee(self) -> bool:
        return self.role_name == "committee"

    @property
    def is_active(self) -> bool:
        # Flask-Login: deactivated users can't sign in
        return self.deleted_at is None

    def has_permission(self, key: str) -> bool:
        if self.is_admin:
            return True
        role = self.role
        if role is None:
            return False
        return role.has_permission(key)


# ---------------------------------------------------------------------------
# Role + permissions
# ---------------------------------------------------------------------------

class Role(db.Model):
    __tablename__ = "roles"

    name = db.Column(db.String(32), primary_key=True)
    label = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(255))

    permissions = db.relationship(
        "RolePermission",
        backref="role",
        lazy="joined",
        cascade="all, delete-orphan",
    )

    def permission_keys(self) -> set[str]:
        return {p.permission_key for p in self.permissions}

    def has_permission(self, key: str) -> bool:
        if self.name == "admin":
            return True
        granted = {p.permission_key for p in self.permissions}
        if key in granted:
            return True
        for g in granted:
            if key in IMPLICIT_PERMISSIONS.get(g, ()):
                return True
        return False


class RolePermission(db.Model):
    """One row per (role, permission key) — only granted permissions stored."""
    __tablename__ = "role_permissions"

    id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(
        db.String(32),
        db.ForeignKey("roles.name", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    permission_key = db.Column(db.String(80), nullable=False, index=True)

    __table_args__ = (
        db.UniqueConstraint("role_name", "permission_key",
                            name="uq_role_permission"),
    )


# ---------------------------------------------------------------------------
# Ensure built-in roles exist on every connection. Idempotent.
# ---------------------------------------------------------------------------

def ensure_roles_exist():
    """Create the four built-in roles if they don't exist. Idempotent."""
    defaults = {
        "unregistered": (
            "Unregistered",
            "Has an email on file but hasn't completed sign-up. No privileges by default.",
        ),
        "member": (
            "Member",
            "Fully signed-up member. Permissions toggleable by an admin.",
        ),
        "committee": (
            "Committee",
            "Trusted contributors. Admin chooses which permissions they receive.",
        ),
        "admin": (
            "Administrator",
            "Full access. Cannot be edited — implicitly holds every permission.",
        ),
    }
    for name, (label, desc) in defaults.items():
        if not db.session.get(Role, name):
            db.session.add(Role(name=name, label=label, description=desc))
    db.session.commit()
